"""Build and compare portable models from verified official source locks."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .source_lock import canonical_json_bytes, verify_source_lock

_RELEVANT = re.compile(r"^(?:DMA0|GPIO[0-4]|PORT[0-4]|LPUART[02]|OSTIMER0|MRCC0|SCG0)$")
_REPRODUCTION_INSTANCES = (
    "DMA0",
    "GPIO0",
    "GPIO1",
    "GPIO2",
    "GPIO3",
    "GPIO4",
    "LPUART0",
    "LPUART2",
    "MRCC0",
    "OSTIMER0",
    "PORT0",
    "PORT1",
    "PORT2",
    "PORT3",
    "PORT4",
    "SCG0",
)
_REPRODUCTION_INTERRUPTS = (
    "GPIO0",
    "GPIO1",
    "GPIO2",
    "GPIO3",
    "GPIO4",
    "LPUART0",
    "OS_EVENT",
    "SCG0",
)
_REPRODUCTION_PACKAGES = {
    "MCXA156": ("MCXA156VFT", "MCXA156VLH", "MCXA156VLL", "MCXA156VMP", "MCXA156VPJ"),
    "MCXA266": ("MCXA266VLH", "MCXA266VLL", "MCXA266VLQ", "MCXA266VPN"),
}
_REPRODUCTION_PINS = {
    "MCXA156": (
        ("P0_2", "LPUART0_RXD"),
        ("P0_3", "LPUART0_TXD"),
        ("P3_13", "GPIO3_13"),
    ),
    "MCXA266": (
        ("P2_2", "LPUART2_TXD"),
        ("P2_3", "LPUART2_RXD"),
        ("P3_19", "GPIO3_19"),
    ),
}
_KNOWN_REGISTER_MAP_DIFFERENCES = {
    "/ip/DMA0/register_map": (
        (5, "447bcd71192d6e77c4ab283a5294d406b3a20953ee6db399b033f3301582169f"),
        (12, "e765d210b8c8260a7a7fce55416540ec28f85ce22e4a436c95736985f4635110"),
    ),
    **{
        f"/ip/GPIO{index}/register_map": (
            (45, "fd8c7787bada2729bb37a1fbae2c158d9826425192e40c35f9fd2e211d708400"),
            (19, "ca04392c50db2b1c3747bdbb9ba0f82f3675a97bfbc867ebc450e09e9589ea64"),
        )
        for index in range(5)
    },
    "/ip/MRCC0/register_map": (
        (86, "a6e5f7d40027c4d42f35a47772c8b84b8dec08e06b47164fa0f424741553891f"),
        (12, "3132f7195877849dbe8d5ad6e34faeef74fe3c44e878543723515fe6d1051a70"),
    ),
    "/ip/OSTIMER0/register_map": (
        (7, "3307820812bea4678001c393c52b0cf46c5034d0d77e47a07af893188663f2d4"),
        (6, "e7528b0913ea33e58cb0b1785af078201c8ae05799ef7ef11232606b3649ca42"),
    ),
    "/ip/PORT0/register_map": (
        (30, "c86bc72293eed1898a98026fb3113c310d141dd044e742ca6eb9adf26bc1cdf6"),
        (5, "5a9b065b1eae52b91c4427650c6f224e32ad0010cf69cc9df2f4b8c20304621c"),
    ),
    "/ip/PORT1/register_map": (
        (29, "1e6f2dc8b20021b0bca9e0d8fa66b1569b60bbccd1ebe90e79b57a5a6c6a5391"),
        (5, "5a9b065b1eae52b91c4427650c6f224e32ad0010cf69cc9df2f4b8c20304621c"),
    ),
    "/ip/PORT2/register_map": (
        (31, "1a97d7c938fe5e0093bd505cd1ce9cce590f8f77348cd259d62525f3864d1f7d"),
        (5, "5a9b065b1eae52b91c4427650c6f224e32ad0010cf69cc9df2f4b8c20304621c"),
    ),
    "/ip/PORT3/register_map": (
        (38, "66de3a5245e7fa9319348ccfee49197f9d77770f8f4443cdc31a7ea0468f4453"),
        (5, "5a9b065b1eae52b91c4427650c6f224e32ad0010cf69cc9df2f4b8c20304621c"),
    ),
    "/ip/PORT4/register_map": (
        (14, "038a80ac77c6c83c794a29b23efea86888227dc42a7ce4dcfe0bd4dc08d0a9bd"),
        (5, "5a9b065b1eae52b91c4427650c6f224e32ad0010cf69cc9df2f4b8c20304621c"),
    ),
    "/ip/SCG0/register_map": (
        (26, "43155b68cc17c01549df8ed78a7114bc579261d08a8583315fa53973fcd2f171"),
        (39, "cf1bdcf18d7ba491f7d5223298e0d61db7d9a34679c70b2d0b80018ad9f81116"),
    ),
}
_KNOWN_COMPATIBILITY_PREFIXES = (
    "/boards/",
    "/clocks/",
    "/dma/",
    "/flash/",
    "/global_pin_scope",
    "/instances/",
    "/interrupts/",
    "/ip/",
    "/linker_regions/",
    "/memories/",
    "/packages/",
    "/pins/",
    "/priority_bits",
    "/resets/",
)
_HEX = re.compile(r"^0[xX][0-9A-Fa-f]+$")
ModelRecord = dict[str, Any]


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
    try:
        jsonschema.validate(model, _model_schema())
    except jsonschema.ValidationError as exc:
        raise ModelError(f"normalized model failed schema v1 validation: {exc.message}") from exc
    validate_model_semantics(model)
    Path(output).write_bytes(canonical_json_bytes(model))
    return model


def compare_models(*, left: str | Path, right: str | Path, output: str | Path) -> dict[str, Any]:
    """Compare model-v0 or an nxp-pac metadata adapter with stable findings."""
    left_path, right_path = Path(left), Path(right)
    left_value = _load_comparison_input(left_path)
    right_value = _load_comparison_input(right_path)
    portable_pair = _is_portable_model(left_value) and _is_portable_model(right_value)
    _validate_comparison_model(left_value)
    _validate_comparison_model(right_value)
    left_projection = _comparison_projection(left_value)
    right_projection = _comparison_projection(right_value)
    findings: list[dict[str, Any]] = []
    keys = _comparison_keys(left_value, right_value, left_projection, right_projection)
    for key in keys:
        left_fact = left_projection.get(key)
        right_fact = right_projection.get(key)
        if left_fact == right_fact:
            continue
        classification, disposition, status = _difference_disposition(
            key, left_fact, right_fact, portable_pair
        )
        finding_payload = {"field": key, "left": left_fact, "right": right_fact}
        finding_id = (
            "difference:" + hashlib.sha256(canonical_json_bytes(finding_payload)).hexdigest()[:24]
        )
        finding = {
            "classification": classification,
            "disposition": disposition,
            "field": key,
            "id": finding_id,
            "left": left_fact,
            "right": right_fact,
            "status": status,
        }
        if portable_pair:
            finding.update(_compatibility_evidence(left_value, right_value))
        findings.append(finding)
    coverage = _coverage(left_value, right_value)
    report = {
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "coverage": coverage,
        "findings": findings,
        "left": {"path": left_path.name, "sha256": _file_sha256(left_path)},
        "report_version": "0",
        "right": {"path": right_path.name, "sha256": _file_sha256(right_path)},
        "scope": _comparison_scope(left_value, right_value, keys),
        "summary": {
            "classified": sum(item["status"] == "classified" for item in findings),
            "equal": not findings,
            "equal_facts": sum(
                left_projection.get(key) == right_projection.get(key) for key in keys
            ),
            "total_differences": len(findings),
            "unclassified": sum(item["status"] == "unresolved" for item in findings),
        },
    }
    Path(output).write_bytes(canonical_json_bytes(report))
    return report


def _difference_disposition(
    key: str, left: object, right: object, portable_pair: bool
) -> tuple[str, str, str]:
    if left is None or right is None:
        if portable_pair:
            return (
                "absent",
                "conservative policy: derivative-only fact is not reusable",
                "classified",
            )
        return "missing", "selected v1 reproduction fact is missing", "unresolved"
    if portable_pair and key.startswith(_KNOWN_COMPATIBILITY_PREFIXES):
        return (
            "incompatible",
            "conservative v1 policy: non-exact fact must not be reused unchanged",
            "classified",
        )
    if portable_pair:
        return "unknown-portable-delta", "new comparison path requires policy review", "unresolved"
    if _is_evidence_backed_reproduction_difference(key, left, right):
        return (
            "known-oracle-difference",
            "exact reviewed v1 representation rule matches both oracle values",
            "classified",
        )
    return "mismatch", "requires source or adapter correction", "unresolved"


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
        _canonicalize_derivative(derivative)
    result["derivatives"].sort(key=lambda item: item["device"])
    for package in result["packages"]:
        package["capabilities"].sort(key=lambda item: item["name"])
        for pin in package["pins"]:
            pin["signals"].sort(key=lambda item: (item["mux"], item["name"]))
        package["pins"].sort(key=lambda item: (item["pad"], item["position"]))
    result["packages"].sort(key=lambda item: (item["device"], item["sku"]))
    for board in result["boards"]:
        board["resources"].sort(key=lambda item: (item["type"], item["name"]))
    result["boards"].sort(key=lambda item: item["id"])
    result["provenance"].sort(key=lambda item: item["id"])
    for conflict in result["conflicts"]:
        conflict["evidence"].sort()
    result["conflicts"].sort(key=lambda item: item["id"])
    _sort_provenance_refs(result)
    result["fact_provenance"] = _fact_provenance(result)
    result["model_id"] = _identity(result, "model_id")
    return result


def _canonicalize_derivative(derivative: ModelRecord) -> None:
    derivative["cores"].sort(key=lambda item: item["name"])
    for memory in derivative["memories"]:
        memory["cores"].sort()
    derivative["memories"].sort(key=lambda item: (int(item["address"], 16), item["name"]))
    derivative["linker_regions"].sort(
        key=lambda item: (int(item["address"], 16), item["name"], item["condition"] or "")
    )
    for pin in derivative["global_pins"]:
        pin["signals"].sort(key=lambda item: (item["mux"], item["name"]))
    derivative["global_pins"].sort(key=lambda item: item["name"])
    for instance in derivative["instances"]:
        instance["clock_ids"].sort()
        instance["reset_ids"].sort()
    derivative["instances"].sort(key=lambda item: (int(item["address"], 16), item["name"]))
    derivative["interrupts"].sort(key=lambda item: (item["number"], item["name"]))
    derivative["dma_requests"].sort(
        key=lambda item: (item["controller"], item["number"], item["name"])
    )
    for clock in derivative["clocks"]:
        clock["parents"].sort()
    derivative["clocks"].sort(key=lambda item: item["id"])
    derivative["resets"].sort(key=lambda item: item["id"])
    derivative["flash"]["timing_rows"].sort(
        key=lambda item: (
            item["mode"],
            item["basis"],
            item["voltage_min_mv"] if item["voltage_min_mv"] is not None else -1,
            item["voltage_max_mv"] if item["voltage_max_mv"] is not None else -1,
            item["temperature_max_c"] if item["temperature_max_c"] is not None else -1,
            item["frequency_hz"],
            item["wait_states"],
        )
    )
    derivative["capabilities"].sort(key=lambda item: item["name"])


def validate_model_semantics(model: dict[str, Any]) -> None:
    """Enforce cross-reference, identity, provenance, and hash invariants."""
    if model.get("model_id") != _identity(model, "model_id"):
        raise ModelError("model_id does not match canonical projection")
    _validate_record_uniqueness(model)
    provenance_ids = _validate_provenance_records(model)
    _validate_model_references(model)
    expected = _fact_provenance(model)
    actual = sorted(model["fact_provenance"], key=lambda item: item["pointer"])
    if any(not set(item["provenance_refs"]) <= provenance_ids for item in actual):
        raise ModelError("fact provenance contains a dangling reference")
    if expected != actual:
        raise ModelError("field-level provenance coverage is incomplete")


def _validate_record_uniqueness(model: ModelRecord) -> None:
    _require_unique(model["derivatives"], lambda item: item["device"], "derivative device")
    _require_unique(model["ip_blocks"], lambda item: item["id"], "IP block ID")
    _require_unique(
        model["packages"], lambda item: (item["device"], item["sku"]), "package device/SKU"
    )
    _require_unique(model["boards"], lambda item: item["id"], "board ID")
    _require_unique(model["conflicts"], lambda item: item["id"], "conflict ID")
    _require_unique(
        model["fact_provenance"], lambda item: item["pointer"], "fact-provenance pointer"
    )
    for block in model["ip_blocks"]:
        _require_unique(block["registers"], lambda item: item["name"], "register name")
        _require_unique(block["registers"], lambda item: item["offset"], "register offset")
        for register in block["registers"]:
            _require_unique(register["fields"], lambda item: item["name"], "field name")
            _require_unique(register["fields"], lambda item: item["bit_offset"], "field offset")
    for derivative in model["derivatives"]:
        _validate_derivative_uniqueness(derivative)


def _validate_derivative_uniqueness(derivative: ModelRecord) -> None:
    collections = (
        ("cores", "name"),
        ("memories", "name"),
        ("global_pins", "name"),
        ("instances", "name"),
        ("interrupts", "name"),
        ("dma_requests", "name"),
        ("clocks", "id"),
        ("resets", "id"),
        ("capabilities", "name"),
    )
    for collection, key in collections:
        _require_unique(
            derivative[collection], lambda item, key=key: item[key], f"{collection} {key}"
        )
    _require_unique(
        derivative["linker_regions"],
        lambda item: (item["image"], item["name"], item["condition"]),
        "linker-region image/name/condition",
    )


def _require_unique(
    records: list[ModelRecord], key: Callable[[ModelRecord], object], label: str
) -> None:
    values = [key(item) for item in records]
    if len(set(values)) != len(values):
        raise ModelError(f"duplicate {label}")


def _validate_provenance_records(model: dict[str, Any]) -> set[str]:
    provenance_ids = {item["id"] for item in model["provenance"]}
    if len(provenance_ids) != len(model["provenance"]):
        raise ModelError("duplicate provenance ID")
    lock_ids = set(model["source_lock_ids"])
    if any(
        item.get("source_lock_id") not in lock_ids
        for item in model["provenance"]
        if item["source_kind"] != "policy"
    ):
        raise ModelError("provenance references a missing source lock")
    if any(
        "source_lock_id" in item for item in model["provenance"] if item["source_kind"] == "policy"
    ):
        raise ModelError("policy provenance must not claim an upstream source lock")
    return provenance_ids


def _validate_model_references(model: dict[str, Any]) -> None:
    for block in model["ip_blocks"]:
        if block["semantic_hash"] != _semantic_hash(block["registers"]):
            raise ModelError(f"IP semantic hash mismatch: {block['id']}")
    block_ids = {item["id"] for item in model["ip_blocks"]}
    devices = {item["device"] for item in model["derivatives"]}
    packages = {(item["device"], item["sku"]) for item in model["packages"]}
    if any(package["device"] not in devices for package in model["packages"]):
        raise ModelError("package references a missing derivative")
    for derivative in model["derivatives"]:
        _validate_derivative_references(derivative, model["boards"], block_ids, packages)
    if any(board["device"] not in devices for board in model["boards"]):
        raise ModelError("board references a missing derivative")


def _validate_derivative_references(
    derivative: ModelRecord,
    boards: list[ModelRecord],
    block_ids: set[str],
    packages: set[tuple[str, str]],
) -> None:
    instances = {item["name"] for item in derivative["instances"]}
    cores = {item["name"] for item in derivative["cores"]}
    clocks = {item["id"] for item in derivative["clocks"]}
    resets = {item["id"] for item in derivative["resets"]}
    pins = {item["name"]: item["signals"] for item in derivative["global_pins"]}
    if any(item["ip_block_id"] not in block_ids for item in derivative["instances"]):
        raise ModelError("instance references a missing IP block")
    if any(not set(item["clock_ids"]) <= clocks for item in derivative["instances"]):
        raise ModelError("instance references a missing clock")
    if any(not set(item["reset_ids"]) <= resets for item in derivative["instances"]):
        raise ModelError("instance references a missing reset")
    if any(not set(item["parents"]) <= clocks for item in derivative["clocks"]):
        raise ModelError("clock references a missing parent")
    if any(not set(item["cores"]) <= cores for item in derivative["memories"]):
        raise ModelError("memory references a missing core")
    _validate_dma_references(derivative["dma_requests"], instances)
    for board in (item for item in boards if item["device"] == derivative["device"]):
        _validate_board_references(board, instances, pins, packages)


def _validate_dma_references(requests: list[ModelRecord], instances: set[str]) -> None:
    if any(item["controller"] not in instances for item in requests):
        raise ModelError("DMA request references a missing controller")
    if any(item["instance"] not in instances for item in requests):
        raise ModelError("DMA request references a missing peripheral instance")


def _validate_board_references(
    board: ModelRecord,
    instances: set[str],
    pins: dict[str, list[ModelRecord]],
    packages: set[tuple[str, str]],
) -> None:
    if (board["device"], board["package_sku"]) not in packages:
        raise ModelError("board references a missing package")
    for resource in board["resources"]:
        if resource["type"] == "led":
            pin = resource["pin"]
            port, number = pin[1:].split("_", 1)
            if (f"GPIO{port}_{number}", 0) not in _pin_signals(pins.get(pin, [])):
                raise ModelError("board LED references a missing pin signal or mux")
        if resource["type"] == "uart":
            if resource["instance"] not in instances:
                raise ModelError("board UART references a missing instance")
            for direction in ("rx", "tx"):
                route = resource[direction]
                signal = f"{resource['instance']}_{direction.upper()}D"
                if (signal, route["mux"]) not in _pin_signals(pins.get(route["pin"], [])):
                    raise ModelError("board UART references a missing pin signal or mux")


def _pin_signals(signals: list[ModelRecord]) -> set[tuple[str, int]]:
    return {(item["name"], item["mux"]) for item in signals}


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
            if project == "mcux-soc-svd":
                source_kind = "svd"
            elif path.endswith(f"/{self.lock['request']['device'].upper()}_COMMON.h"):
                source_kind = "cmsis"
            else:
                source_kind = "sdk"
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
    blocks, instances, svd_interrupts = _svd_projection(root, device, svd_ref)
    cmsis_text, cmsis_ref = inputs.text(f"/{device}_COMMON.h")
    startup_suffix = (
        f"/{device}/gcc/startup_{device}.S"
        if device == "MCXA156"
        else f"/{device}/startup_{device}.c"
    )
    startup_text, startup_ref = inputs.text(startup_suffix)
    interrupts = _interrupts(cmsis_text, cmsis_ref, startup_text, startup_ref, svd_interrupts)
    priority_bits = _priority_bits(cmsis_text, cmsis_ref, priority_bits, svd_ref)
    flash_linker_text, flash_linker_ref = inputs.text(f"/{device}/gcc/{device}_flash.ld")
    ram_linker_text, ram_linker_ref = inputs.text(f"/{device}/gcc/{device}_ram.ld")
    clock_text, clock_ref = inputs.text(f"/{device}/drivers/fsl_clock.h")
    reset_text, reset_ref = inputs.text(f"/{device}/drivers/fsl_reset.h")
    variable_text, variable_ref = inputs.text(f"/{device}/variable.cmake")
    dma_dir = re.search(r"soc_periph\s+([A-Za-z0-9_]+)", variable_text)
    if dma_dir is None:
        raise ModelError("device variable.cmake does not select soc_periph")
    dma_text, dma_ref = inputs.text(f"MCXA/{dma_dir.group(1)}/PERI_DMA.h")
    dma_soc_text, dma_soc_ref = inputs.text(f"/{device}/drivers/fsl_edma_soc.h")
    board_text, board_ref = _board_pin_source(inputs, device)
    board_header, board_header_ref = inputs.text("/board.h", "mcu-sdk-examples")
    board = _board(device, board_text, board_ref, board_header, board_header_ref)
    policy_ref = "policy.normalized-model-v1"
    global_pins = _board_required_pins(_global_pins(board_text, board_ref), board)
    derivative = {
        "capabilities": _capabilities(chip, chip_ref),
        "clocks": _clocks(clock_text, clock_ref, chip),
        "cores": _cores(chip, chip_ref),
        "device": device,
        "dma_requests": _dma(dma_text, dma_ref, dma_soc_text, dma_soc_ref),
        "flash": _flash(device, inputs),
        "global_pin_scope": {
            "provenance_refs": [policy_ref],
            "value": "board-required-subset",
        },
        "global_pins": global_pins,
        "instances": _attach_gates(instances, clock_text, clock_ref, reset_text, reset_ref),
        "interrupts": interrupts,
        "linker_regions": _linker_regions(
            flash_linker_text,
            flash_linker_ref,
            ram_linker_text,
            ram_linker_ref,
        ),
        "memories": _memories(chip, chip_ref),
        "priority_bits": priority_bits,
        "provenance_refs": [
            chip_ref,
            svd_ref,
            cmsis_ref,
            startup_ref,
            flash_linker_ref,
            ram_linker_ref,
            clock_ref,
            reset_ref,
            variable_ref,
            dma_ref,
            dma_soc_ref,
        ],
        "resets": _resets(reset_text, reset_ref),
    }
    return _assemble_model(lock, inputs, derivative, board, blocks, chip, chip_ref, policy_ref)


def _assemble_model(
    lock: ModelRecord,
    inputs: _Inputs,
    derivative: ModelRecord,
    board: ModelRecord,
    blocks: list[ModelRecord],
    chip: ModelRecord,
    chip_ref: str,
    policy_ref: str,
) -> ModelRecord:
    packages = [
        {
            "bond_out_status": "unavailable",
            "capabilities": [],
            "device": derivative["device"],
            "package": item["name"],
            "pins": [],
            "provenance_refs": [chip_ref, policy_ref],
            "sku": item["name"],
        }
        for item in chip["part"]
    ]
    return {
        "boards": [board],
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "conflicts": [],
        "derivatives": [derivative],
        "fact_provenance": [],
        "ip_blocks": blocks,
        "model_id": "sha256:" + "0" * 64,
        "packages": packages,
        "provenance": [*inputs.provenance(), _policy_provenance(policy_ref)],
        "schema_version": "1",
        "source_lock_ids": [lock["lock_id"]],
    }


def _policy_provenance(policy_ref: str) -> ModelRecord:
    resource = files("nxp_monkey").joinpath("schemas/normalized_model.policy.v1.txt")
    payload = resource.read_bytes()
    return {
        "id": policy_ref,
        "input_path": "docs/contracts/normalized_model.policy.v1.txt",
        "locator": "nxp-monkey:docs/contracts/normalized_model.policy.v1.txt",
        "project": "nxp-monkey",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source_kind": "policy",
    }


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


def _priority_bits(cmsis_text: str, cmsis_ref: str, svd_value: int, svd_ref: str) -> ModelRecord:
    cmsis_value = int(
        _required_match(r"#define\s+__NVIC_PRIO_BITS\s+(\d+)U?", cmsis_text, "CMSIS priority bits")
    )
    if cmsis_value != svd_value:
        raise ModelError(f"CMSIS/SVD NVIC priority mismatch: {cmsis_value} != {svd_value}")
    return {"provenance_refs": [cmsis_ref, svd_ref], "value": cmsis_value}


def _interrupts(
    cmsis_text: str,
    cmsis_ref: str,
    startup_text: str,
    startup_ref: str,
    svd_interrupts: list[ModelRecord],
) -> list[ModelRecord]:
    cmsis = {
        name: int(number)
        for name, number in re.findall(
            r"^\s*([A-Za-z][A-Za-z0-9_]*)_IRQn\s*=\s*(-?\d+)",
            cmsis_text,
            re.MULTILINE,
        )
        if int(number) >= 0
    }
    if not cmsis:
        raise ModelError("CMSIS header has no device interrupts")
    startup = _startup_interrupts(startup_text)
    mismatched_startup = sorted(
        name for name, number in cmsis.items() if startup.get(name) != number
    )
    if mismatched_startup:
        raise ModelError(f"startup vector slot mismatches CMSIS interrupts: {mismatched_startup}")
    svd = {item["name"]: item for item in svd_interrupts}
    result = []
    for name, number in cmsis.items():
        refs = [cmsis_ref, startup_ref]
        svd_item = svd.get(name)
        if svd_item is not None:
            if svd_item["number"] != number:
                raise ModelError(
                    f"CMSIS/SVD interrupt mismatch for {name}: {number} != {svd_item['number']}"
                )
            refs.extend(svd_item["provenance_refs"])
        result.append({"name": name.upper(), "number": number, "provenance_refs": refs})
    return result


def _startup_interrupts(text: str) -> dict[str, int]:
    if "__Vectors:" in text:
        table = _required_match(r"__Vectors:(.*?)\.size\s+__Vectors", text, "startup vector")
        entries = re.findall(r"^\s*\.long\s+([A-Za-z0-9_]+)", table, re.MULTILINE)
        if len(entries) < 17:
            raise ModelError("startup vector has no device interrupt slots")
        return {
            name.removesuffix("_IRQHandler"): index
            for index, name in enumerate(entries[16:])
            if name.endswith("_IRQHandler")
        }
    numbered = re.findall(
        r"^\s*([A-Za-z][A-Za-z0-9_]*)_IRQHandler\s*,\s*//\s*(\d+)\s*:",
        text,
        re.MULTILINE,
    )
    if not numbered:
        raise ModelError("startup vector has no numbered device interrupt slots")
    return {name: int(slot) - 16 for name, slot in numbered}


def _linker_regions(
    flash_text: str,
    flash_ref: str,
    ram_text: str,
    ram_ref: str,
) -> list[ModelRecord]:
    regions = _parse_linker_memory(flash_text, flash_ref, "flash")
    regions.extend(_parse_linker_memory(ram_text, ram_ref, "ram"))
    return regions


def _parse_linker_memory(text: str, ref: str, image: str) -> list[ModelRecord]:
    block = _required_match(r"\bMEMORY\s*\{(.*?)\}", text, f"{image} linker MEMORY block")
    rows = []
    matches = re.findall(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([A-Za-z]+)\)\s*:\s*"
        r"ORIGIN\s*=\s*([^,\r\n]+)\s*,\s*LENGTH\s*=\s*([^\r\n]+)",
        block,
        re.MULTILINE,
    )
    for name, access, address, size in matches:
        addresses = _linker_expression_values(address, text)
        sizes = _linker_expression_values(size, text)
        if set(addresses) != set(sizes):
            raise ModelError(f"{image} linker region {name} has mismatched conditions")
        for condition in addresses:
            rows.append(
                {
                    "access": access.lower(),
                    "address": _hex(addresses[condition]),
                    "condition": condition,
                    "image": image,
                    "name": name,
                    "provenance_refs": [ref],
                    "size": sizes[condition],
                }
            )
    if not rows:
        raise ModelError(f"{image} linker MEMORY block has no regions")
    declared = len(re.findall(r"\bORIGIN\s*=", block))
    if declared != len(matches):
        raise ModelError(f"{image} linker MEMORY block contains an unparsed region")
    required = "m_text" if image == "flash" else "m_data"
    if required not in {item["name"] for item in rows}:
        raise ModelError(f"{image} linker MEMORY block lacks required {required} region")
    return rows


def _linker_expression_values(expression: str, text: str) -> dict[str | None, int]:
    expression = expression.strip()
    if re.fullmatch(r"0x[0-9A-Fa-f]+", expression):
        return {None: int(expression, 0)}
    match = re.fullmatch(r"(0x[0-9A-Fa-f]+)\s*([+-])\s*RAM_OFFSET", expression)
    offset = re.search(
        r"RAM_OFFSET\s*=\s*DEFINED\(__pkc__\)\s*\?\s*(0x[0-9A-Fa-f]+)\s*:\s*0",
        text,
    )
    if match is None or offset is None:
        raise ModelError(f"unsupported linker expression: {expression}")
    base, operation = int(match.group(1), 0), match.group(2)
    delta = int(offset.group(1), 0)
    defined = base + delta if operation == "+" else base - delta
    return {"__pkc__ defined": defined, "__pkc__ undefined": base}


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
    for name in _gate_values(text):
        if _RELEVANT.fullmatch(name):
            clocks.append(
                {
                    "id": f"{name.lower()}-gate",
                    "kind": "gate",
                    "max_frequency_hz": None,
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
            "max_frequency_hz": None,
            "parents": parents,
            "provenance_refs": [ref],
            "selector": "kCLOCK_SelLPUART0",
        }
    )
    clocks.extend(
        {
            "id": parent,
            "kind": "source",
            "max_frequency_hz": None,
            "parents": [],
            "provenance_refs": [ref],
            "selector": None,
        }
        for parent in parents
    )
    return clocks


def _resets(text: str, ref: str) -> list[dict[str, Any]]:
    resets = []
    for name, (group, bit) in _reset_values(text).items():
        if _RELEVANT.fullmatch(name):
            resets.append(
                {
                    "active_level": "low",
                    "bit": str(bit),
                    "id": f"{name.lower()}-reset",
                    "provenance_refs": [ref],
                    "register": f"MRCC.GLB_RST{group}",
                }
            )
    return resets


def _attach_gates(
    instances: list[dict[str, Any]], clock: str, clock_ref: str, reset: str, reset_ref: str
) -> list[dict[str, Any]]:
    gates = _gate_values(clock)
    reset_values = _reset_values(reset)
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
                "config": None,
                "enable_register": f"MRCC.GLB_CC{gate_group}",
                "reset_register": f"MRCC.GLB_RST{reset_group}",
            }
            instance["provenance_refs"].extend([clock_ref, reset_ref])
    return instances


def _gate_values(text: str) -> dict[str, tuple[int, int]]:
    values = {}
    for name, expression in re.findall(
        r"kCLOCK_Gate([A-Z0-9]+)\s*=\s*(.+?),\s*/\*!< Clock gate name:", text
    ):
        shifted = re.search(r"0x([0-9A-Fa-f]+)U\s*<<\s*16U", expression)
        literal = re.search(r"0x([0-9A-Fa-f]{4})U", expression)
        group = int(shifted.group(1), 16) // 0x10 if shifted else 0
        if shifted is None and literal is not None:
            group = int(literal.group(1), 16) >> 16
        bit = int(_required_match(r"\((\d+)U\)\s*\)+$", expression, f"{name} gate bit"))
        values[name] = (group, bit)
    return values


def _reset_values(text: str) -> dict[str, tuple[int, int]]:
    values = {}
    for name, expression in re.findall(r"k([A-Z0-9]+)_RST_SHIFT_RSTn\s*=\s*(.+?),\s*/\*!<", text):
        shifted = re.search(r"(\d+)U\s*<<\s*8U", expression)
        group = int(shifted.group(1)) if shifted else 0
        bit = int(_required_match(r"\(?(\d+)U\)?\s*\)+$", expression, f"{name} reset bit"))
        values[name] = (group, bit)
    return values


def _dma(text: str, ref: str, dma_soc_text: str, dma_soc_ref: str) -> list[ModelRecord]:
    dma_version = _required_match(
        r"#define\s+FSL_EDMA_SOC_IP_(DMA\d+)\s+\(1\)",
        dma_soc_text,
        "enabled DMA IP version",
    )
    requests = []
    for controller, signal, number in re.findall(
        r"kDma(\d+)RequestLPUART0(Rx|Tx)\s*=\s*(\d+)U", text
    ):
        requests.append(
            {
                "controller": f"DMA{controller}",
                "instance": "LPUART0",
                "mux": dma_version,
                "name": f"LPUART0_{signal.upper()}",
                "number": int(number),
                "provenance_refs": [ref, dma_soc_ref],
                "signal": signal.upper(),
            }
        )
    if {item["signal"] for item in requests} != {"RX", "TX"}:
        raise ModelError("official DMA table lacks LPUART0 RX/TX requests")
    return requests


def _flash(device: str, inputs: _Inputs) -> dict[str, Any]:
    if device == "MCXA266":
        text, ref = inputs.text(f"/{device}/drivers/fsl_clock.c")
        rows = _mcxa266_flash_rows(text, ref, 240_000_000)
    else:
        text, ref = inputs.text("/clock_config.c", "mcu-sdk-examples")
        rows = _mcxa156_flash_rows(text, ref)
    return {"provenance_refs": [ref], "timing_rows": rows}


def _mcxa156_flash_rows(text: str, ref: str) -> list[dict[str, Any]]:
    markers = list(re.finditer(r"Configuration BOARD_BootClockFRO(\d+)M", text))
    values = set()
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        segment = text[marker.end() : end]
        wait = re.search(r"FMU_FCTRL_RWSC\(0x([0-9A-Fa-f]+)U\)", segment)
        if wait is not None:
            values.add((int(marker.group(1)), int(wait.group(1), 16)))
    if not values:
        raise ModelError("MCXA156 board clock source has no parseable flash timing rows")
    expected_frequencies = {12, 24, 48, 64, 96}
    if {frequency for frequency, _wait in values} != expected_frequencies:
        raise ModelError("MCXA156 board clock source lacks the complete configured frequency set")
    return [_timing("configured", "normal", mhz * 1_000_000, wait, ref) for mhz, wait in values]


def _mcxa266_flash_rows(text: str, ref: str, device_max: int) -> list[dict[str, Any]]:
    function = _required_match(
        r"(CLOCK_SetFLASHAccessCyclesForFreq\(.*?\n\})(?=\n\s*/\* Get SYSTEM)",
        text,
        "MCXA266 flash timing function",
    )
    rows = []
    for mode in ("MD", "OD"):
        block = _required_match(
            rf"case \(uint32_t\)k{mode}_Mode:(.*?)break;", function, f"{mode} timing block"
        )
        fail = re.search(r"system_freq_hz > (\d+)U\).*?return kStatus_Fail", block, re.DOTALL)
        upper = int(fail.group(1)) if fail else device_max
        branches = re.findall(
            r"system_freq_hz > (\d+)U\)\s*\{\s*num_wait_states_added = (\d+)U;", block
        )
        for threshold, wait_states in branches:
            rows.append(_timing("upper-bound", mode, upper, int(wait_states), ref))
            upper = int(threshold)
        rows.append(_timing("upper-bound", mode, upper, 0, ref))
    return rows


def _timing(basis: str, mode: str, frequency: int, waits: int, ref: str) -> dict[str, Any]:
    return {
        "basis": basis,
        "frequency_hz": frequency,
        "mode": mode,
        "provenance_refs": [ref],
        "temperature_max_c": None,
        "voltage_max_mv": None,
        "voltage_min_mv": None,
        "wait_states": waits,
    }


def _board_pin_source(inputs: _Inputs, device: str) -> tuple[str, str]:
    return inputs.text(f"/frdm{device.lower()}/common/pin_mux/pin_mux.c", "mcu-sdk-examples")


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
    configured = re.findall(
        r"const port_pin_config_t\s+[A-Za-z0-9_]+\s*=\s*\{(.*?)\};\s*"
        r"/\*\s*PORT(\d+)_(\d+).*?configured as ([A-Za-z0-9_]+)\s*\*/",
        text,
        flags=re.DOTALL,
    )
    for block, port, number, configured_as in configured:
        mux_match = re.search(r"kPORT_MuxAlt(\d+)", block)
        if mux_match is None:
            continue
        if configured_as == f"P{port}_{number}":
            signal = f"GPIO{port}_{number}"
        elif re.fullmatch(r"LPUART\d+_(?:RXD|TXD)", configured_as):
            signal = configured_as
        else:
            continue
        pin = f"P{port}_{number}"
        fact = {"mux": int(mux_match.group(1)), "name": signal, "provenance_refs": [ref]}
        if fact not in pins.setdefault(pin, []):
            pins[pin].append(fact)
    return [
        {"name": name, "provenance_refs": [ref], "signals": signals}
        for name, signals in pins.items()
    ]


def _board_required_pins(pins: list[ModelRecord], board: ModelRecord) -> list[ModelRecord]:
    by_name = {item["name"]: item for item in pins}
    for resource in board["resources"]:
        if resource["type"] != "led":
            continue
        pin = resource["pin"]
        port, number = pin[1:].split("_", 1)
        expected = (f"GPIO{port}_{number}", 0)
        actual = {
            (signal["name"], signal["mux"]) for signal in by_name.get(pin, {}).get("signals", [])
        }
        if expected not in actual:
            raise ModelError(f"board pin source lacks exact LED mux for {pin}")
    required = {resource["pin"] for resource in board["resources"] if resource["type"] == "led"}
    for resource in board["resources"]:
        if resource["type"] == "uart":
            required.update((resource["rx"]["pin"], resource["tx"]["pin"]))
    return [by_name[name] for name in required]


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
    led_on = int(_required_match(r"LOGIC_LED_ON\s+(\d+)U", header, "LED active level"))
    if led_on not in (0, 1):
        raise ModelError("LED active level is not binary")
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
                "active_level": "low" if led_on == 0 else "high",
                "name": "green",
                "pin": f"P{port}_{pin}",
                "provenance_refs": [header_ref],
                "type": "led",
            },
            {
                "baud": baud,
                "instance": instance,
                "name": "vcom",
                "provenance_refs": [pin_ref, header_ref],
                "rx": uart_pins["RXD"],
                "tx": uart_pins["TXD"],
                "type": "uart",
            },
        ],
    }


def _comparison_projection(value: dict[str, Any]) -> dict[str, Any]:
    if "_rust_projection" in value:
        return value["_rust_projection"]
    if value.get("schema_version") == "1" and "derivatives" in value:
        return _portable_projection(value)
    if "chips" in value and "peripherals" in value:
        return _adapter_projection(value)
    raise ModelError("unsupported comparison input; expected model-v0 or nxp-pac metadata")


def _portable_projection(value: dict[str, Any]) -> dict[str, Any]:
    derivative = value["derivatives"][0]
    projection: dict[str, Any] = {
        "/global_pin_scope": derivative["global_pin_scope"]["value"],
        "/priority_bits": derivative["priority_bits"]["value"],
    }
    for item in derivative["memories"]:
        projection[f"/memories/{item['name']}"] = [item["address"], item["size"]]
    for item in derivative["linker_regions"]:
        condition = item["condition"] or "unconditional"
        projection[f"/linker_regions/{item['image']}/{item['name']}/{condition}"] = [
            item["address"],
            item["size"],
            item["access"],
        ]
    for item in derivative["instances"]:
        projection[f"/instances/{item['name']}/address"] = item["address"].lower()
        projection[f"/instances/{item['name']}/gate"] = _gate_projection(item["gate"])
    for item in derivative["interrupts"]:
        projection[f"/interrupts/{item['name']}"] = item["number"]
    for item in derivative["dma_requests"]:
        projection[f"/dma/{item['name']}"] = {
            "controller": item["controller"],
            "instance": item["instance"],
            "mux": item["mux"],
            "number": item["number"],
            "signal": item["signal"],
        }
    for item in value["packages"]:
        projection[f"/packages/{item['sku']}"] = item["bond_out_status"]
    projection.update(_portable_pin_projection(derivative))
    projection.update(_portable_hardware_projection(value, derivative))
    return projection


def _gate_projection(gate: ModelRecord | None) -> ModelRecord | None:
    if gate is None:
        return None
    return {
        "config": gate["config"],
        "enable": gate["enable_register"].replace("MRCC.GLB_", "mrcc_glb_").lower(),
        "reset": gate["reset_register"].replace("MRCC.GLB_", "mrcc_glb_").lower(),
    }


def _portable_pin_projection(derivative: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for item in derivative["global_pins"]:
        for signal in item["signals"]:
            projection[f"/pins/{item['name']}/{signal['name']}"] = signal["mux"]
    return projection


def _portable_hardware_projection(
    value: dict[str, Any], derivative: dict[str, Any]
) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for item in value["ip_blocks"]:
        instance_name = item["id"].rsplit(".", 1)[-1].upper()
        projection[f"/ip/{instance_name}/register_map"] = _register_map_signature(item["registers"])
    for item in derivative["clocks"]:
        projection[f"/clocks/{item['id']}"] = _without_provenance(item)
    for item in derivative["resets"]:
        projection[f"/resets/{item['id']}"] = _without_provenance(item)
    projection["/flash/timing_rows"] = _without_provenance(derivative["flash"]["timing_rows"])
    for board in value["boards"]:
        projection[f"/boards/{board['id']}/package_sku"] = board["package_sku"]
        for resource in board["resources"]:
            key = f"/boards/{board['id']}/{resource['type']}/{resource['name']}"
            projection[key] = _without_provenance(resource)
    return projection


def _compatibility_evidence(left: ModelRecord, right: ModelRecord) -> ModelRecord:
    def source(model: ModelRecord, side: str) -> ModelRecord:
        return {
            "locators": sorted({item["locator"] for item in model["provenance"]}),
            "model_id": model["model_id"],
            "side": side,
            "source_lock_ids": model["source_lock_ids"],
        }

    return {
        "evidence": [left["model_id"], right["model_id"]],
        "owner": "nxp-pac-and-embassy-board/pac-reconciliation",
        "sources": [source(left, "left"), source(right, "right")],
    }


def _adapter_kind(left: ModelRecord, right: ModelRecord) -> str:
    value = right if _is_portable_model(left) else left
    return str(value.get("_adapter_kind", "metadata"))


def _is_portable_model(value: dict[str, Any]) -> bool:
    return value.get("schema_version") == "1" and "derivatives" in value


def _without_provenance(value: Any) -> Any:
    projected = copy.deepcopy(value)
    _remove_key(projected, "provenance_refs")
    return projected


def _adapter_projection(value: dict[str, Any]) -> dict[str, Any]:
    projection = {"/priority_bits": value.get("nvic_prio_bits")}
    projection.update(_adapter_memory_projection(value.get("chips", [])))
    projection.update(_adapter_peripheral_projection(value["peripherals"]))
    for sku in value.get("chips", []):
        if isinstance(sku, str):
            projection[f"/packages/{sku}"] = "unavailable"
    for name, number in value.get("interrupts", {}).items():
        projection[f"/interrupts/{name}"] = int(number)
    return projection


def _adapter_memory_projection(chips: Any) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    if isinstance(chips, dict):
        chip = next(iter(chips.values()))
        for item in chip.get("memory", chip.get("memories", [])):
            name = item.get("name") or item.get("kind", "unknown")
            projection[f"/memories/{name}"] = [
                _normalize_hex(item.get("address", item.get("start", 0))),
                int(item.get("size", 0)),
            ]
    return projection


def _adapter_peripheral_projection(peripherals: Any) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    entries = peripherals.values() if isinstance(peripherals, dict) else peripherals
    for item in entries:
        projection.update(_adapter_peripheral_facts(item))
    return projection


def _adapter_peripheral_facts(item: ModelRecord) -> ModelRecord:
    projection: ModelRecord = {}
    name = item.get("name")
    if name and "address" in item:
        projection[f"/instances/{name}/address"] = _normalize_hex(item["address"])
        projection[f"/instances/{name}/gate"] = _adapter_gate(item.get("gate"))
    for dma in item.get("dma_muxing", []):
        dma_name = str(dma.get("signal", dma.get("name", "")))
        dma_name = dma_name.removeprefix(str(name)).upper()
        if name and dma_name:
            projection[f"/dma/{name}_{dma_name}"] = {
                "controller": "DMA0",
                "instance": name,
                "mux": dma.get("mux"),
                "number": int(dma.get("request", dma.get("request_number", 0))),
                "signal": dma_name,
            }
    for signal in item.get("signals", []):
        for pin in signal.get("pins", []):
            projection[f"/pins/{pin['pin']}/{name}_{signal['name']}"] = int(pin["alt"])
    return projection


def _adapter_gate(gate: object) -> ModelRecord | None:
    if not isinstance(gate, dict):
        return None
    return {
        "config": gate.get("config"),
        "enable": str(gate.get("enable")).lower(),
        "reset": str(gate["reset"]).lower() if gate.get("reset") is not None else None,
    }


def _load_comparison_input(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".rs":
        value = json.loads(path.read_text(encoding="utf-8"))
        if "chips" in value and "peripherals" in value:
            value["_adapter_kind"] = "metadata"
        return value
    text = path.read_text(encoding="utf-8")
    priority = _required_match(r"NVIC_PRIO_BITS:\s*u8\s*=\s*(\d+)", text, "NVIC priority")
    projection: dict[str, Any] = {"/priority_bits": int(priority)}
    for name, number in re.findall(r"^\s{4}([A-Z][A-Z0-9_]+)\s*=\s*(\d+),", text, re.MULTILINE):
        projection[f"/interrupts/{name}"] = int(number)
    for name, address in re.findall(
        r"^pub const ([A-Z][A-Z0-9_]+):.*?from_ptr\((0x[0-9A-Fa-f]+) as _\)",
        text,
        re.MULTILINE,
    ):
        projection[f"/instances/{name}/address"] = _normalize_hex(address)
    projection.update(_rust_register_projections(path, text))
    return {"_adapter_kind": "rust", "_rust_projection": projection}


def _register_map_signature(registers: list[ModelRecord]) -> ModelRecord:
    rows = sorted(
        (
            item["name"].lower().rstrip("_"),
            _normalize_hex(item["offset"]),
            item["access"],
            item["width_bits"],
        )
        for item in registers
    )
    return {
        "count": len(rows),
        "sha256": "sha256:" + hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }


def _rust_register_projections(path: Path, chip_text: str) -> ModelRecord:
    module_paths = {
        module: relative
        for relative, module in re.findall(
            r'#\[path\s*=\s*"([^"]+)"\]\s*pub mod\s+([a-zA-Z0-9_]+)\s*;',
            chip_text,
        )
    }
    instance_modules = {
        name: module
        for name, module in re.findall(
            r"^pub const ([A-Z][A-Z0-9_]+):\s*([a-zA-Z0-9_]+)::",
            chip_text,
            re.MULTILINE,
        )
        if _RELEVANT.fullmatch(name)
    }
    signatures: ModelRecord = {}
    module_cache: dict[str, ModelRecord] = {}
    for instance, module in instance_modules.items():
        relative = module_paths.get(module)
        if relative is None:
            continue
        if module not in module_cache:
            module_path = (path.parent / relative).resolve()
            module_cache[module] = _rust_register_map_signature(
                module_path.read_text(encoding="utf-8")
            )
        signatures[f"/ip/{instance}/register_map"] = module_cache[module]
    return signatures


def _rust_register_map_signature(text: str) -> ModelRecord:
    access = {"R": "read-only", "W": "write-only", "RW": "read-write"}
    rows = []
    pattern = re.compile(
        r"pub const fn\s+([a-zA-Z0-9_]+)\s*\(\s*self(?:\s*,\s*n:\s*usize)?\s*\)"
        r"\s*->\s*crate::pac::common::Reg<[^,>]+,\s*crate::pac::common::(R|W|RW)>"
        r".*?wrapping_add\((0x[0-9A-Fa-f]+)usize",
        re.DOTALL,
    )
    for name, mode, offset in pattern.findall(text):
        rows.append((name.rstrip("_"), _normalize_hex(offset), access[mode], 32))
    rows.sort()
    if not rows:
        raise ModelError("generated Rust peripheral module has no register map")
    return {
        "count": len(rows),
        "sha256": "sha256:" + hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }


def _validate_comparison_model(value: ModelRecord) -> None:
    if value.get("schema_version") == "0" and "derivatives" in value:
        raise ModelError(
            "normalized-model schema v0 cannot be silently reinterpreted; "
            "re-normalize its retained source lock to schema v1"
        )
    if not _is_portable_model(value):
        return
    try:
        jsonschema.validate(value, _model_schema())
        validate_model_semantics(value)
    except (jsonschema.ValidationError, ModelError) as exc:
        raise ModelError(f"comparison input model is invalid: {exc}") from exc


def _model_schema() -> ModelRecord:
    schema = files("nxp_monkey").joinpath("schemas/normalized_model.schema.v1.json")
    return json.loads(schema.read_text(encoding="utf-8"))


def _comparison_keys(
    left: ModelRecord,
    right: ModelRecord,
    left_projection: ModelRecord,
    right_projection: ModelRecord,
) -> list[str]:
    if _is_portable_model(left) and _is_portable_model(right):
        return sorted(set(left_projection) | set(right_projection))
    return _reproduction_comparison_keys(left, right, left_projection, right_projection)


def _reproduction_comparison_keys(
    left: ModelRecord,
    right: ModelRecord,
    left_projection: ModelRecord,
    right_projection: ModelRecord,
) -> list[str]:
    portable = left if _is_portable_model(left) else right
    adapter = right if _is_portable_model(left) else left
    device = portable["derivatives"][0]["device"]
    portable_projection = left_projection if _is_portable_model(left) else right_projection
    adapter_projection = right_projection if _is_portable_model(left) else left_projection
    candidate = set(left_projection) | set(right_projection)
    prefixes = ["/priority_bits"]
    interrupt_names = {"SCG0", "LPUART0", "OS_EVENT", *(f"GPIO{i}" for i in range(5))}
    adapter_kind = str(adapter.get("_adapter_kind", "metadata"))
    fixed_inventory = _fixed_reproduction_inventory(device, adapter_kind)
    if fixed_inventory is not None:
        return _validated_fixed_inventory(fixed_inventory, adapter_projection, adapter_kind)
    if adapter_kind == "metadata":
        prefixes.extend(["/dma/LPUART0_", f"/packages/{device}"])
    selected = {
        key
        for key in candidate
        if _selected_reproduction_key(
            key, portable_projection, adapter_kind, prefixes, interrupt_names
        )
    }
    if adapter_kind == "rust":
        selected |= {
            key for key in candidate if key.startswith("/ip/") and key in portable_projection
        }
    return sorted(selected)


def _validated_fixed_inventory(
    inventory: set[str], adapter_projection: ModelRecord, adapter_kind: str
) -> list[str]:
    missing = sorted(inventory - set(adapter_projection))
    if missing:
        raise ModelError(f"{adapter_kind} comparison oracle lacks required v1 facts: {missing}")
    return sorted(inventory)


def _fixed_reproduction_inventory(device: str, adapter_kind: str) -> set[str] | None:
    """Return the contract-owned inventory without consulting generated model contents."""
    packages = _REPRODUCTION_PACKAGES.get(device)
    pins = _REPRODUCTION_PINS.get(device)
    if packages is None or pins is None:
        return None
    inventory = {"/priority_bits"}
    inventory.update(f"/interrupts/{name}" for name in _REPRODUCTION_INTERRUPTS)
    inventory.update(f"/instances/{name}/address" for name in _REPRODUCTION_INSTANCES)
    if adapter_kind == "rust":
        inventory.update(f"/ip/{name}/register_map" for name in _REPRODUCTION_INSTANCES)
        return inventory
    inventory.update(f"/instances/{name}/gate" for name in _REPRODUCTION_INSTANCES)
    inventory.update({"/dma/LPUART0_RX", "/dma/LPUART0_TX"})
    inventory.update(f"/packages/{sku}" for sku in packages)
    inventory.update(f"/pins/{pin}/{signal}" for pin, signal in pins)
    return inventory


def _selected_reproduction_key(
    key: str,
    portable: ModelRecord,
    adapter_kind: str,
    prefixes: list[str],
    interrupts: set[str],
) -> bool:
    if any(key.startswith(prefix) for prefix in prefixes):
        return True
    if key not in portable:
        return False
    if key.removeprefix("/interrupts/") in interrupts:
        return True
    if key.startswith("/pins/"):
        return adapter_kind == "metadata"
    if not key.startswith("/instances/"):
        return False
    instance = key.split("/")[2]
    return _RELEVANT.fullmatch(instance) is not None and (
        adapter_kind == "metadata" or key.endswith("/address")
    )


def _comparison_scope(left: ModelRecord, right: ModelRecord, keys: list[str]) -> ModelRecord:
    if _is_portable_model(left) and _is_portable_model(right):
        return {
            "inventory": "mcxa-portable-compatibility-v0",
            "not_compared": [
                "core_details",
                "derivative_capabilities",
                "package_pin_bondout",
            ],
            "pin_scope": "board-required-subset",
            "selected_fact_count": len(keys),
        }
    adapter_kind = _adapter_kind(left, right)
    not_compared = ["boards", "capabilities", "flash_timing", "linker_regions", "memories"]
    if adapter_kind == "metadata":
        not_compared.append("register_maps")
    else:
        not_compared.extend(["dma_requests", "gates", "packages", "pins"])
    return {
        "inventory": f"mcxa-board-increment-{adapter_kind}-v0",
        "not_compared": sorted(not_compared),
        "pin_scope": "board-required-subset",
        "selected_fact_count": len(keys),
    }


def _is_evidence_backed_reproduction_difference(field: str, left: object, right: object) -> bool:
    if field.startswith("/ip/") and field.endswith("/register_map"):
        return _is_known_register_map_difference(field, left, right)
    if not field.endswith("/gate"):
        return False
    return _is_known_gate_difference(field, left, right)


def _is_known_register_map_difference(field: str, left: object, right: object) -> bool:
    expected = _KNOWN_REGISTER_MAP_DIFFERENCES.get(field)
    actual = (_register_signature_tuple(left), _register_signature_tuple(right))
    return expected is not None and actual in (expected, expected[::-1])


def _is_known_gate_difference(field: str, left: object, right: object) -> bool:
    values = (left, right)
    if not all(isinstance(item, dict) for item in values):
        return False
    left_gate = left if isinstance(left, dict) else {}
    right_gate = right if isinstance(right, dict) else {}
    expected_config = (
        "LpuartConfig"
        if field in {"/instances/LPUART0/gate", "/instances/LPUART2/gate"}
        else "OsTimerConfig"
        if field == "/instances/OSTIMER0/gate"
        else None
    )
    configs = {left_gate.get("config"), right_gate.get("config")}
    if configs not in ({None}, {None, expected_config}):
        return False
    if left_gate.get("reset") != right_gate.get("reset"):
        return False
    enables = {str(left_gate.get("enable")), str(right_gate.get("enable"))}
    return len(enables) == 1 or any(
        enables == {f"mrcc_glb_cc{group}", f"mrcc_glb_acc{group}"} for group in range(4)
    )


def _register_signature_tuple(value: object) -> tuple[int, str] | None:
    if not isinstance(value, dict):
        return None
    count, digest = value.get("count"), value.get("sha256")
    if not isinstance(count, int) or not isinstance(digest, str):
        return None
    return count, digest.removeprefix("sha256:")


def _coverage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, dict[str, int]]:
    categories = (
        "memories",
        "linker_regions",
        "interrupts",
        "ip_blocks",
        "instances",
        "dma_requests",
        "clocks",
        "resets",
        "packages",
        "boards",
        "global_pins",
    )

    def counts(value: dict[str, Any]) -> dict[str, int]:
        if "derivatives" not in value:
            return _adapter_counts(value)
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


def _adapter_counts(value: dict[str, Any]) -> dict[str, int]:
    if "_rust_projection" in value:
        projection = value["_rust_projection"]
        return {
            "memories": 0,
            "linker_regions": 0,
            "interrupts": sum(key.startswith("/interrupts/") for key in projection),
            "ip_blocks": sum(key.startswith("/ip/") for key in projection),
            "instances": sum(key.endswith("/address") for key in projection),
            "dma_requests": 0,
            "clocks": 0,
            "resets": 0,
            "packages": 0,
            "boards": 0,
            "global_pins": 0,
        }
    peripherals = value.get("peripherals", {})
    entries = peripherals.values() if isinstance(peripherals, dict) else peripherals
    entries = list(entries)
    chips = value.get("chips", [])
    memories = 0
    if isinstance(chips, dict) and chips:
        memories = len(next(iter(chips.values())).get("memory", []))
    return {
        "memories": memories,
        "linker_regions": 0,
        "interrupts": len(value.get("interrupts", {})),
        "ip_blocks": 0,
        "instances": sum("address" in item for item in entries),
        "dma_requests": sum(len(item.get("dma_muxing", [])) for item in entries),
        "clocks": 0,
        "resets": 0,
        "packages": len(chips),
        "boards": 0,
        "global_pins": len(value.get("pins", [])),
    }


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
    match = re.search(pattern, value, flags=re.DOTALL)
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
