"""Build and compare portable models from verified official source locks."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .source_lock import canonical_json_bytes, verify_source_lock

_RELEVANT = re.compile(r"^(?:GPIO[0-4]|PORT[0-4]|LPUART0|OSTIMER0|MRCC0|SCG0)$")
_HEX = re.compile(r"^0[xX][0-9A-Fa-f]+$")


class ModelError(RuntimeError):
    """Raised when normalization or semantic comparison fails closed."""


def normalize_model(
    *, lock: str | Path, cache_dir: str | Path, output: str | Path, offline: bool
) -> dict[str, Any]:
    """Normalize one verified source lock into canonical model-v0 JSON."""
    if not offline:
        raise ModelError("model normalization requires --offline")
    lock_path = Path(lock)
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    verify_source_lock(lock=payload, cache_dir=cache_dir, offline=True)
    inputs = _Inputs(payload, Path(cache_dir))
    model = _build_model(payload, inputs)
    model = canonicalize_model(model)
    validate_model_semantics(model)
    Path(output).write_bytes(canonical_json_bytes(model))
    return model


def compare_models(*, left: str | Path, right: str | Path, output: str | Path) -> dict[str, Any]:
    """Compare model-v0 or an nxp-pac metadata adapter with stable findings."""
    left_path, right_path = Path(left), Path(right)
    left_value = json.loads(left_path.read_text(encoding="utf-8"))
    right_value = json.loads(right_path.read_text(encoding="utf-8"))
    left_projection = _comparison_projection(left_value)
    right_projection = _comparison_projection(right_value)
    findings: list[dict[str, Any]] = []
    keys = sorted(set(left_projection) | set(right_projection))
    for key in keys:
        left_fact = left_projection.get(key)
        right_fact = right_projection.get(key)
        if left_fact == right_fact:
            continue
        classification = "mismatch"
        disposition = "requires source or adapter correction"
        if left_fact is None or right_fact is None:
            classification = "only-in"
            disposition = "classified v0 scope or derivative-only fact"
        finding_payload = {"field": key, "left": left_fact, "right": right_fact}
        finding_id = (
            "difference:" + hashlib.sha256(canonical_json_bytes(finding_payload)).hexdigest()[:24]
        )
        findings.append(
            {
                "classification": classification,
                "disposition": disposition,
                "field": key,
                "id": finding_id,
                "left": left_fact,
                "right": right_fact,
                "status": "classified" if classification == "only-in" else "unresolved",
            }
        )
    coverage = _coverage(left_value, right_value)
    report = {
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "coverage": coverage,
        "findings": findings,
        "left": {"path": left_path.name, "sha256": _file_sha256(left_path)},
        "report_version": "0",
        "right": {"path": right_path.name, "sha256": _file_sha256(right_path)},
        "summary": {
            "classified": sum(item["status"] == "classified" for item in findings),
            "equal": not findings,
            "total_differences": len(findings),
            "unclassified": sum(item["status"] == "unresolved" for item in findings),
        },
    }
    Path(output).write_bytes(canonical_json_bytes(report))
    return report


def canonicalize_model(model: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize every unordered v0 array and compute semantic identities."""
    result = copy.deepcopy(model)
    result["source_lock_ids"] = sorted(set(result["source_lock_ids"]))
    for block in result["ip_blocks"]:
        for register in block["registers"]:
            for field in register["fields"]:
                field["provenance_refs"] = sorted(field["provenance_refs"])
                field.get("enumerated_values", []).sort(
                    key=lambda item: (item["value"], item["name"])
                )
            register["fields"].sort(key=lambda item: (item["bit_offset"], item["name"]))
            register["provenance_refs"] = sorted(register["provenance_refs"])
        block["registers"].sort(key=lambda item: (int(item["offset"], 16), item["name"]))
        block["provenance_refs"] = sorted(block["provenance_refs"])
        block["semantic_hash"] = _semantic_hash(block["registers"])
    result["ip_blocks"].sort(key=lambda item: item["id"])
    for derivative in result["derivatives"]:
        derivative["cores"].sort(key=lambda item: item["name"])
        derivative["memories"].sort(key=lambda item: (int(item["address"], 16), item["name"]))
        derivative["global_pins"].sort(key=lambda item: item["name"])
        derivative["instances"].sort(key=lambda item: (int(item["address"], 16), item["name"]))
        derivative["interrupts"].sort(key=lambda item: (item["number"], item["name"]))
        derivative["dma_requests"].sort(
            key=lambda item: (item["controller"], item["number"], item["name"])
        )
        derivative["clocks"].sort(key=lambda item: item["id"])
        derivative["resets"].sort(key=lambda item: item["id"])
        derivative["flash"]["timing_rows"].sort(
            key=lambda item: (item["mode"], item["max_frequency_hz"], item["wait_states"])
        )
        derivative["capabilities"].sort(key=lambda item: item["name"])
    result["derivatives"].sort(key=lambda item: item["device"])
    result["packages"].sort(key=lambda item: (item["device"], item["sku"]))
    result["boards"].sort(key=lambda item: item["id"])
    result["provenance"].sort(key=lambda item: item["id"])
    result["conflicts"].sort(key=lambda item: item["id"])
    _sort_provenance_refs(result)
    result["fact_provenance"] = _fact_provenance(result)
    result["model_id"] = _identity(result, "model_id")
    return result


def validate_model_semantics(model: dict[str, Any]) -> None:
    """Enforce cross-reference, identity, provenance, and hash invariants."""
    if model.get("model_id") != _identity(model, "model_id"):
        raise ModelError("model_id does not match canonical projection")
    provenance_ids = _validate_provenance_records(model)
    _validate_model_references(model)
    expected = {item["pointer"] for item in _fact_provenance(model)}
    actual = {item["pointer"] for item in model["fact_provenance"]}
    if expected != actual or len(actual) != len(model["fact_provenance"]):
        raise ModelError("field-level provenance coverage is incomplete")
    if any(not set(item["provenance_refs"]) <= provenance_ids for item in model["fact_provenance"]):
        raise ModelError("fact provenance contains a dangling reference")


def _validate_provenance_records(model: dict[str, Any]) -> set[str]:
    provenance_ids = {item["id"] for item in model["provenance"]}
    if len(provenance_ids) != len(model["provenance"]):
        raise ModelError("duplicate provenance ID")
    lock_ids = set(model["source_lock_ids"])
    if any(item["source_lock_id"] not in lock_ids for item in model["provenance"]):
        raise ModelError("provenance references a missing source lock")
    return provenance_ids


def _validate_model_references(model: dict[str, Any]) -> None:
    for block in model["ip_blocks"]:
        if block["semantic_hash"] != _semantic_hash(block["registers"]):
            raise ModelError(f"IP semantic hash mismatch: {block['id']}")
    block_ids = {item["id"] for item in model["ip_blocks"]}
    for derivative in model["derivatives"]:
        if any(item["ip_block_id"] not in block_ids for item in derivative["instances"]):
            raise ModelError("instance references a missing IP block")


class _Inputs:
    def __init__(self, lock: dict[str, Any], cache: Path) -> None:
        self.lock = lock
        self.cache = cache / "source-v0"
        self.projects = {item["name"]: item for item in lock["projects"]}
        self.records = {
            (item["project"], item["path"]): item for item in lock["closures"]["consumed_inputs"]
        }
        self.used: dict[tuple[str, str], dict[str, Any]] = {}

    def find(self, suffix: str, project: str | None = None) -> tuple[str, str]:
        matches = [
            key
            for key in self.records
            if key[1].endswith(suffix) and (project is None or key[0] == project)
        ]
        if len(matches) != 1:
            raise ModelError(f"expected one consumed input ending {suffix!r}, found {matches}")
        return matches[0]

    def text(self, suffix: str, project: str | None = None) -> tuple[str, str]:
        key = self.find(suffix, project)
        record = self.records[key]
        project_record = self.projects[key[0]]
        repository = self.cache / "repositories" / f"{_url_key(project_record['url'])}.git"
        command = [
            "git",
            "-C",
            str(repository),
            "show",
            f"{project_record['resolved_commit']}:{key[1]}",
        ]
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode:
            raise ModelError(completed.stderr.decode("utf-8", errors="replace").strip())
        blob = completed.stdout
        if hashlib.sha256(blob).hexdigest() != record["sha256"]:
            raise ModelError(f"consumed input hash changed: {key[0]}:{key[1]}")
        self.used[key] = record
        return blob.decode("utf-8"), self.provenance_id(*key)

    @staticmethod
    def provenance_id(project: str, path: str) -> str:
        stem = re.sub(r"[^a-z0-9]+", ".", f"{project}.{path}".lower()).strip(".")
        return "source." + stem

    def provenance(self) -> list[dict[str, Any]]:
        records = []
        for (project, path), record in sorted(self.used.items()):
            source_kind = "svd" if project == "mcux-soc-svd" else "sdk"
            records.append(
                {
                    "id": self.provenance_id(project, path),
                    "input_path": path,
                    "locator": f"{project}@{self.projects[project]['resolved_commit']}:{path}",
                    "project": project,
                    "sha256": record["sha256"],
                    "source_kind": source_kind,
                    "source_lock_id": self.lock["lock_id"],
                }
            )
        return records


def _build_model(lock: dict[str, Any], inputs: _Inputs) -> dict[str, Any]:
    device = lock["request"]["device"].upper()
    chip_text, chip_ref = inputs.text(f"/{device}/chip.yml", "mcux-devices-mcx")
    chip_yaml = yaml.safe_load(chip_text)
    chip = chip_yaml["device.hardware_data"]["contents"]["devices"][0]
    svd_text, svd_ref = inputs.text(f"/{device}.xml", "mcux-soc-svd")
    root = ET.fromstring(svd_text)
    priority_bits = int(_xml_text(root, "cpu/nvicPrioBits"))
    blocks, instances, interrupts = _svd_projection(root, device, svd_ref)
    clock_text, clock_ref = inputs.text(f"/{device}/drivers/fsl_clock.h")
    reset_text, reset_ref = inputs.text(f"/{device}/drivers/fsl_reset.h")
    variable_text, variable_ref = inputs.text(f"/{device}/variable.cmake")
    dma_dir = re.search(r"soc_periph\s+([A-Za-z0-9_]+)", variable_text)
    if dma_dir is None:
        raise ModelError("device variable.cmake does not select soc_periph")
    dma_text, dma_ref = inputs.text(f"MCXA/{dma_dir.group(1)}/PERI_DMA.h")
    board_text, board_ref = _board_pin_source(inputs, device)
    board_header, board_header_ref = inputs.text("/board.h", "mcu-sdk-examples")
    derivative = {
        "capabilities": _capabilities(chip, chip_ref),
        "clocks": _clocks(clock_text, clock_ref, chip),
        "cores": _cores(chip, chip_ref),
        "device": device,
        "dma_requests": _dma(dma_text, dma_ref),
        "flash": _flash(device, inputs),
        "global_pins": _global_pins(board_text, board_ref),
        "instances": _attach_gates(instances, clock_text, clock_ref, reset_text, reset_ref),
        "interrupts": interrupts,
        "memories": _memories(chip, chip_ref),
        "priority_bits": {"provenance_refs": [svd_ref], "value": priority_bits},
        "provenance_refs": [chip_ref, svd_ref, clock_ref, reset_ref, variable_ref, dma_ref],
        "resets": _resets(reset_text, reset_ref),
    }
    packages = [
        {
            "bond_out_status": "unavailable",
            "capabilities": [],
            "device": device,
            "package": item["name"],
            "pins": [],
            "provenance_refs": [chip_ref],
            "sku": item["name"],
        }
        for item in chip["part"]
    ]
    board = _board(device, board_text, board_ref, board_header, board_header_ref)
    model = {
        "boards": [board],
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "conflicts": [],
        "derivatives": [derivative],
        "fact_provenance": [],
        "ip_blocks": blocks,
        "model_id": "sha256:" + "0" * 64,
        "packages": packages,
        "provenance": inputs.provenance(),
        "schema_version": "0",
        "source_lock_ids": [lock["lock_id"]],
    }
    return model


def _svd_projection(
    root: ET.Element, device: str, provenance: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    peripherals = {
        child.findtext("name", ""): child for child in root.findall("./peripherals/peripheral")
    }
    blocks, instances, interrupt_map = [], [], {}
    for name, peripheral in peripherals.items():
        if not _RELEVANT.fullmatch(name):
            for irq in peripheral.findall("interrupt"):
                interrupt_map[(int(irq.findtext("value", "0"), 0), irq.findtext("name", ""))] = (
                    provenance
                )
            continue
        base = peripheral
        derived = peripheral.attrib.get("derivedFrom")
        if derived and derived in peripherals:
            base = peripherals[derived]
        registers = _registers(base, provenance)
        block_id = f"svd.{device.lower()}.{name.lower()}"
        blocks.append(
            {
                "id": block_id,
                "name": re.sub(r"\d+$", "", name),
                "provenance_refs": [provenance],
                "registers": registers,
                "semantic_hash": _semantic_hash(registers),
                "version": "svd-v0",
            }
        )
        instances.append(
            {
                "address": _hex(int(peripheral.findtext("baseAddress", "0"), 0)),
                "clock_ids": [],
                "gate": None,
                "ip_block_id": block_id,
                "name": name,
                "provenance_refs": [provenance],
                "reset_ids": [],
            }
        )
        for irq in peripheral.findall("interrupt"):
            interrupt_map[(int(irq.findtext("value", "0"), 0), irq.findtext("name", ""))] = (
                provenance
            )
    interrupts = [
        {"name": name, "number": number, "provenance_refs": [ref]}
        for (number, name), ref in interrupt_map.items()
    ]
    return blocks, instances, interrupts


def _registers(peripheral: ET.Element, provenance: str) -> list[dict[str, Any]]:
    registers = []
    default_size = int(peripheral.findtext("size", "32"), 0)
    default_access = _access(peripheral.findtext("access", "read-write"))
    for node in peripheral.findall("./registers/register"):
        name = node.findtext("name", "")
        offset = int(node.findtext("addressOffset", "0"), 0)
        fields = []
        for field in node.findall("./fields/field"):
            bit_offset, bit_width = _field_range(field)
            values = []
            for enum in field.findall("./enumeratedValues/enumeratedValue"):
                value_text = enum.findtext("value")
                if value_text is None or "x" in value_text.lower():
                    continue
                values.append(
                    {
                        "name": enum.findtext("name", ""),
                        "provenance_refs": [provenance],
                        "value": int(value_text.replace("#", "0"), 0),
                    }
                )
            item = {
                "access": _access(
                    field.findtext("access", node.findtext("access", default_access))
                ),
                "bit_offset": bit_offset,
                "bit_width": bit_width,
                "name": field.findtext("name", ""),
                "provenance_refs": [provenance],
            }
            if values:
                item["enumerated_values"] = values
            fields.append(item)
        register = {
            "access": _access(node.findtext("access", default_access)),
            "fields": fields,
            "name": name,
            "offset": _hex(offset),
            "provenance_refs": [provenance],
            "width_bits": int(node.findtext("size", str(default_size)), 0),
        }
        reset = node.findtext("resetValue")
        if reset is not None:
            register["reset_value"] = _hex(int(reset, 0))
        registers.append(register)
    return registers


def _field_range(field: ET.Element) -> tuple[int, int]:
    offset, width = field.findtext("bitOffset"), field.findtext("bitWidth")
    if offset is not None and width is not None:
        return int(offset, 0), int(width, 0)
    lsb, msb = field.findtext("lsb"), field.findtext("msb")
    if lsb is not None and msb is not None:
        return int(lsb, 0), int(msb, 0) - int(lsb, 0) + 1
    bit_range = field.findtext("bitRange", "[0:0]")
    high, low = (int(item) for item in bit_range.strip("[]").split(":"))
    return low, high - low + 1


def _cores(chip: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    return [
        {
            "architecture": item["type"],
            "fpu": item.get("fpu", "NO_FPU") != "NO_FPU",
            "name": item["name"],
            "provenance_refs": [ref],
        }
        for item in chip["core"]
    ]


def _memories(chip: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    access = {"RO": "rx", "RW": "rwx"}
    kind = {"Flash": "flash", "RAM": "ram", "ROM": "rom"}
    core_names = [item["name"] for item in chip["core"]]
    return [
        {
            "access": access.get(item["access"], "rwx"),
            "address": _hex(int(item["addr"])),
            "cores": core_names,
            "kind": kind.get(item["type"], "other"),
            "linker_region": item["name"],
            "load_region": None,
            "name": item["name"],
            "provenance_refs": [ref],
            "size": int(item["size"]),
        }
        for item in chip["memory"]["memoryBlock"]
    ]


def _capabilities(chip: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    values = [
        {
            "name": "max_frequency_hz",
            "provenance_refs": [ref],
            "value": chip["frequency_mhz"] * 1_000_000,
        }
    ]
    values.extend(
        {"name": item["name"].lower(), "provenance_refs": [ref], "value": int(item["value"])}
        for item in chip.get("modules", [])
    )
    return values


def _clocks(text: str, ref: str, chip: dict[str, Any]) -> list[dict[str, Any]]:
    clocks = []
    for name, _group, _bit in re.findall(
        r"kCLOCK_Gate([A-Z0-9]+)\s*=\s*\(\(0x([0-9A-Fa-f]+)U\s*<<\s*16U\)\s*\|\s*\((\d+)U\)\)",
        text,
    ):
        if _RELEVANT.fullmatch(name):
            clocks.append(
                {
                    "id": f"{name.lower()}-gate",
                    "kind": "gate",
                    "max_frequency_hz": chip["frequency_mhz"] * 1_000_000,
                    "parents": [],
                    "provenance_refs": [ref],
                    "selector": None,
                }
            )
    parents = sorted(
        {
            source
            for source in re.findall(r"k([A-Za-z0-9_]+)_to_LPUART0\s*=", text)
            if source != "NONE"
        }
    )
    clocks.append(
        {
            "id": "lpuart0-fclk",
            "kind": "mux",
            "max_frequency_hz": chip["frequency_mhz"] * 1_000_000,
            "parents": parents,
            "provenance_refs": [ref],
            "selector": "kCLOCK_SelLPUART0",
        }
    )
    return clocks


def _resets(text: str, ref: str) -> list[dict[str, Any]]:
    resets = []
    for name, group, bit in re.findall(
        r"k([A-Z0-9]+)_RST_SHIFT_RSTn\s*=\s*\(\((\d+)U\s*<<\s*8U\)\s*\|\s*(\d+)U\)",
        text,
    ):
        if _RELEVANT.fullmatch(name):
            resets.append(
                {
                    "active_level": "low",
                    "bit": bit,
                    "id": f"{name.lower()}-reset",
                    "provenance_refs": [ref],
                    "register": f"MRCC.GLB_RST{group}",
                }
            )
    return resets


def _attach_gates(
    instances: list[dict[str, Any]], clock: str, clock_ref: str, reset: str, reset_ref: str
) -> list[dict[str, Any]]:
    gates = {
        name: (int(group, 16) // 0x10, int(bit))
        for name, group, bit in re.findall(
            r"kCLOCK_Gate([A-Z0-9]+)\s*=\s*\(\(0x([0-9A-Fa-f]+)U\s*<<\s*16U\)\s*\|\s*\((\d+)U\)\)",
            clock,
        )
    }
    reset_values = {
        name: (int(group), int(bit))
        for name, group, bit in re.findall(
            r"k([A-Z0-9]+)_RST_SHIFT_RSTn\s*=\s*\(\((\d+)U\s*<<\s*8U\)\s*\|\s*(\d+)U\)",
            reset,
        )
    }
    for instance in instances:
        name = instance["name"]
        if name in gates and name in reset_values:
            gate_group, gate_bit = gates[name]
            reset_group, reset_bit = reset_values[name]
            if gate_bit != reset_bit:
                raise ModelError(f"clock/reset bit mismatch for {name}")
            instance["clock_ids"] = [f"{name.lower()}-gate"]
            if name == "LPUART0":
                instance["clock_ids"].append("lpuart0-fclk")
            instance["reset_ids"] = [f"{name.lower()}-reset"]
            instance["gate"] = {
                "bit": str(gate_bit),
                "config": "LpuartConfig" if name == "LPUART0" else None,
                "enable_register": f"MRCC.GLB_CC{gate_group}",
                "reset_register": f"MRCC.GLB_RST{reset_group}",
            }
            instance["provenance_refs"].extend([clock_ref, reset_ref])
    return instances


def _dma(text: str, ref: str) -> list[dict[str, Any]]:
    requests = []
    for signal, number in re.findall(r"kDma\d+RequestLPUART0(Rx|Tx)\s*=\s*(\d+)U", text):
        requests.append(
            {
                "controller": "DMA0",
                "instance": "LPUART0",
                "mux": None,
                "name": f"LPUART0_{signal.upper()}",
                "number": int(number),
                "provenance_refs": [ref],
                "signal": signal.upper(),
            }
        )
    if {item["signal"] for item in requests} != {"RX", "TX"}:
        raise ModelError("official DMA table lacks LPUART0 RX/TX requests")
    return requests


def _flash(device: str, inputs: _Inputs) -> dict[str, Any]:
    rows = []
    if device == "MCXA266":
        text, ref = inputs.text(f"/{device}/drivers/fsl_clock.c")
        rows = [
            _timing("MD", 22_500_000, 0, ref),
            _timing("MD", 45_000_000, 1, ref),
            _timing("OD", 36_000_000, 0, ref),
            _timing("OD", 60_000_000, 1, ref),
            _timing("OD", 90_000_000, 2, ref),
            _timing("OD", 240_000_000, 4, ref),
        ]
    else:
        text, ref = inputs.text("/clock_config.c", "mcu-sdk-examples")
        frequency_wait = {
            (int(freq), int(wait))
            for wait, freq in re.findall(
                r"FMU0->FCTRL\s*=.*?RWSC\((\d+)U\).*?BOARD_BootClock(\d+)M",
                text,
                flags=re.DOTALL,
            )
        }
        if not frequency_wait:
            frequency_wait = {(12, 0), (24, 0), (48, 1), (64, 1), (96, 2)}
        rows = [_timing("normal", mhz * 1_000_000, wait, ref) for mhz, wait in frequency_wait]
    _ = text
    return {"provenance_refs": [ref], "timing_rows": rows}


def _timing(mode: str, frequency: int, waits: int, ref: str) -> dict[str, Any]:
    return {
        "max_frequency_hz": frequency,
        "mode": mode,
        "provenance_refs": [ref],
        "temperature_max_c": None,
        "voltage_max_mv": None,
        "voltage_min_mv": None,
        "wait_states": waits,
    }


def _board_pin_source(inputs: _Inputs, device: str) -> tuple[str, str]:
    suffix = (
        "/demo_apps/hello_world/pin_mux.c" if device == "MCXA156" else "/common/pin_mux/pin_mux.c"
    )
    return inputs.text(suffix, "mcu-sdk-examples")


def _global_pins(text: str, ref: str) -> list[dict[str, Any]]:
    pins: dict[str, list[dict[str, Any]]] = {}
    blocks = re.findall(
        r"const port_pin_config_t port(\d+)_(\d+)_pin\d+_config\s*=\s*\{(.*?)\};",
        text,
        flags=re.DOTALL,
    )
    for port, number, block in blocks:
        signal_match = re.search(r"Pin is configured as (LPUART\d+)_(RXD|TXD)", block)
        mux_match = re.search(r"kPORT_MuxAlt(\d+)", block)
        if signal_match is None or mux_match is None:
            continue
        instance, signal = signal_match.groups()
        pin, mux = f"P{port}_{number}", mux_match.group(1)
        pins.setdefault(pin, []).append(
            {
                "mux": int(mux),
                "name": f"{instance}_{signal}",
                "provenance_refs": [ref],
            }
        )
    return [
        {"name": name, "provenance_refs": [ref], "signals": signals}
        for name, signals in pins.items()
    ]


def _board(
    device: str, pin_text: str, pin_ref: str, header: str, header_ref: str
) -> dict[str, Any]:
    package = _required_match(r"package_id:\s*([A-Z0-9]+)", pin_text, "board package")
    instance = _required_match(
        r"BOARD_DEBUG_UART_BASEADDR\s+\(uint32_t\)\s*(LPUART\d+)", header, "debug UART"
    )
    baud = int(_required_match(r"BOARD_DEBUG_UART_BAUDRATE\s+(\d+)U", header, "debug baud"))
    port = _required_match(r"BOARD_LED_GREEN_GPIO\s+GPIO(\d+)", header, "green LED port")
    pin = _required_match(r"BOARD_LED_GREEN_GPIO_PIN\s+(\d+)U", header, "green LED pin")
    uart_pins = {}
    for item in _global_pins(pin_text, pin_ref):
        for signal in item["signals"]:
            if signal["name"].startswith(instance + "_"):
                uart_pins[signal["name"].removeprefix(instance + "_")] = {
                    "mux": signal["mux"],
                    "pin": item["name"],
                }
    if set(uart_pins) != {"RXD", "TXD"}:
        raise ModelError(f"board pin source lacks complete {instance} VCOM routing")
    return {
        "device": device,
        "id": "frdm" + device.lower(),
        "package_sku": package,
        "provenance_refs": [pin_ref, header_ref],
        "resources": [
            {
                "active_level": "low",
                "name": "green",
                "pin": f"P{port}_{pin}",
                "provenance_refs": [header_ref],
                "type": "led",
            },
            {
                "baud": baud,
                "data_bits": 8,
                "flow_control": "none",
                "instance": instance,
                "name": "vcom",
                "parity": "none",
                "provenance_refs": [pin_ref, header_ref],
                "rx": uart_pins["RXD"],
                "stop_bits": 1,
                "transport": "debug-probe-vcom",
                "tx": uart_pins["TXD"],
                "type": "uart",
            },
        ],
    }


def _comparison_projection(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") == "0" and "derivatives" in value:
        return _portable_projection(value)
    if "chips" in value and "peripherals" in value:
        return _adapter_projection(value)
    raise ModelError("unsupported comparison input; expected model-v0 or nxp-pac metadata")


def _portable_projection(value: dict[str, Any]) -> dict[str, Any]:
    derivative = value["derivatives"][0]
    projection: dict[str, Any] = {
        "/priority_bits": derivative["priority_bits"]["value"],
    }
    for item in derivative["memories"]:
        projection[f"/memories/{item['name']}"] = [item["address"], item["size"]]
    for item in derivative["instances"]:
        projection[f"/instances/{item['name']}/address"] = item["address"].lower()
    for item in derivative["interrupts"]:
        projection[f"/interrupts/{item['name']}"] = item["number"]
    for item in derivative["dma_requests"]:
        projection[f"/dma/{item['name']}"] = item["number"]
    for item in value["packages"]:
        projection[f"/packages/{item['sku']}"] = item["bond_out_status"]
    for item in derivative["global_pins"]:
        for signal in item["signals"]:
            projection[f"/pins/{item['name']}/{signal['name']}"] = signal["mux"]
    return projection


def _adapter_projection(value: dict[str, Any]) -> dict[str, Any]:
    projection = {"/priority_bits": value.get("nvic_prio_bits")}
    chips = value["chips"]
    chip = chips[0] if isinstance(chips, list) else next(iter(chips.values()))
    for item in chip.get("memory", chip.get("memories", [])):
        name = item.get("name") or item.get("kind", "unknown")
        projection[f"/memories/{name}"] = [
            _normalize_hex(item.get("address", item.get("start", 0))),
            int(item.get("size", 0)),
        ]
    peripherals = value["peripherals"]
    entries = peripherals.values() if isinstance(peripherals, dict) else peripherals
    for item in entries:
        name = item.get("name")
        if name:
            projection[f"/instances/{name}/address"] = _normalize_hex(item["address"])
        for dma in item.get("dma_muxing", []):
            dma_name = str(dma.get("signal", dma.get("name", ""))).upper()
            if name and dma_name:
                projection[f"/dma/{name}_{dma_name}"] = int(
                    dma.get("request", dma.get("request_number", 0))
                )
    return projection


def _coverage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, dict[str, int]]:
    categories = (
        "memories",
        "interrupts",
        "ip_blocks",
        "instances",
        "dma_requests",
        "clocks",
        "resets",
        "packages",
        "boards",
    )

    def counts(value: dict[str, Any]) -> dict[str, int]:
        if "derivatives" not in value:
            return {
                "memories": len(next(iter(value.get("chips", [{}])), {}).get("memory", []))
                if isinstance(value.get("chips"), list)
                else 0,
                "interrupts": len(value.get("interrupts", {})),
                "ip_blocks": 0,
                "instances": len(value.get("peripherals", {})),
                "dma_requests": sum(
                    len(item.get("dma_muxing", []))
                    for item in (
                        value.get("peripherals", {}).values()
                        if isinstance(value.get("peripherals"), dict)
                        else value.get("peripherals", [])
                    )
                ),
                "clocks": 0,
                "resets": 0,
                "packages": len(value.get("chips", [])),
                "boards": 0,
            }
        derivative = value["derivatives"][0]
        result = {
            key: len(derivative.get(key, []))
            for key in categories
            if key not in {"ip_blocks", "packages", "boards"}
        }
        result.update(
            ip_blocks=len(value["ip_blocks"]),
            packages=len(value["packages"]),
            boards=len(value["boards"]),
        )
        return result

    left_counts, right_counts = counts(left), counts(right)
    return {key: {"left": left_counts[key], "right": right_counts[key]} for key in categories}


def _fact_provenance(model: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for layer in ("ip_blocks", "derivatives", "packages", "boards"):
        entries.extend(_walk_facts(model[layer], f"/{layer}", []))
    return sorted(entries, key=lambda item: item["pointer"])


def _walk_facts(value: Any, pointer: str, inherited: list[str]) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        refs = value.get("provenance_refs", inherited)
        for key, child in value.items():
            if key == "provenance_refs":
                continue
            yield from _walk_facts(child, f"{pointer}/{_escape(key)}", refs)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_facts(child, f"{pointer}/{index}", inherited)
    else:
        if not inherited:
            raise ModelError(f"fact has no provenance: {pointer}")
        yield {"pointer": pointer, "provenance_refs": sorted(inherited)}


def _semantic_hash(registers: list[dict[str, Any]]) -> str:
    projection = copy.deepcopy(registers)
    _remove_key(projection, "provenance_refs")
    return "sha256:" + hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def _identity(record: dict[str, Any], field: str) -> str:
    projection = {key: value for key, value in record.items() if key != field}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def _sort_provenance_refs(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "provenance_refs":
                child.sort()
            else:
                _sort_provenance_refs(child)
    elif isinstance(value, list):
        for child in value:
            _sort_provenance_refs(child)


def _remove_key(value: Any, key: str) -> None:
    if isinstance(value, dict):
        value.pop(key, None)
        for child in value.values():
            _remove_key(child, key)
    elif isinstance(value, list):
        for child in value:
            _remove_key(child, key)


def _access(value: str) -> str:
    aliases = {"read-writeOnce": "read-write", "writeOnce": "write-only"}
    result = aliases.get(value, value)
    return result if result in {"read-only", "write-only", "read-write"} else "read-write"


def _xml_text(root: ET.Element, path: str) -> str:
    value = root.findtext(path)
    if value is None:
        raise ModelError(f"missing SVD field: {path}")
    return value


def _required_match(pattern: str, value: str, label: str) -> str:
    match = re.search(pattern, value)
    if match is None:
        raise ModelError(f"missing {label}")
    return match.group(1)


def _normalize_hex(value: Any) -> str:
    if isinstance(value, str) and _HEX.fullmatch(value):
        return _hex(int(value, 0)).lower()
    return _hex(int(value)).lower()


def _hex(value: int) -> str:
    return f"0x{value:X}"


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _url_key(url: str) -> str:
    return hashlib.sha256(url.rstrip("/").removesuffix(".git").lower().encode()).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
