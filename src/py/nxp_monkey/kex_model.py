"""Normalize a verified KEX acquisition lock into portable model schema v2."""

from __future__ import annotations

import copy
import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Container
from importlib.resources import files
from pathlib import Path

import jsonschema

from ._version import __version__
from .kex_lock import verify_kex_acquisition_lock
from .model import ModelRecord
from .source_lock import canonical_json_bytes

_CANONICALIZATION = "json-sort-keys-indent-2-lf-final-newline-v1"
_FACT_SECTIONS = (
    "clock_gates",
    "device",
    "dma_requests",
    "interrupts",
    "packages",
    "package_commonality",
    "peripherals",
    "pin_model_declarations",
    "pin_routes",
    "programming_sequences",
)
_NUMBER = re.compile(r"^(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|[0-9]+)$")


class KexModelError(RuntimeError):
    """Raised when KEX model-v2 normalization cannot prove its input semantics."""


class _KexInputs:
    def __init__(self, lock: ModelRecord, cache_dir: Path) -> None:
        request = _mapping(lock["request"], "request")
        self.part = _text(request["part"], "request.part")
        self.version = _text(request["upstream_version"], "request.upstream_version")
        self.variants = _string_list(request["variants"], "request.variants")
        self.root = cache_dir / "kex" / self.version
        self.records = {
            _text(item["path"], "raw_files.path"): item
            for item in _records(lock["raw_files"], "raw_files")
        }
        self.used: set[str] = set()

    def relative_path(self, variant: str, relative: str | Path) -> str:
        suffix = Path(relative).as_posix().lstrip("/")
        return f"processors/{self.part}/{variant}/{suffix}"

    def path(self, variant: str, relative: str | Path) -> Path:
        relative_path = self.relative_path(variant, relative)
        if relative_path not in self.records:
            raise KexModelError(f"KEX model input is not locked: {relative_path}")
        self.used.add(relative_path)
        return self.root / Path(relative_path)

    def xml(self, variant: str, relative: str | Path) -> tuple[ET.Element, ModelRecord]:
        relative_path = self.relative_path(variant, relative)
        path = self.path(variant, relative)
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise KexModelError(f"cannot parse locked XML {relative_path}: {exc}") from exc
        return root, self.source(variant, relative, f"/{_local(root.tag)}")

    def source(self, variant: str, relative: str | Path, locator: str) -> ModelRecord:
        relative_path = self.relative_path(variant, relative)
        record = self.records.get(relative_path)
        if record is None:
            raise KexModelError(f"KEX provenance input is not locked: {relative_path}")
        return {"locator": locator, "ref": _source_id(record)}

    def provenance(self) -> list[ModelRecord]:
        result = []
        for path in sorted(self.used):
            record = self.records[path]
            result.append(
                {
                    "id": _source_id(record),
                    "path": path,
                    "sha256": record["sha256"],
                    "variant": record["variant"],
                }
            )
        return result


def normalize_kex_model(
    *,
    lock: str | Path,
    cache_dir: str | Path,
    output: str | Path,
    offline: bool,
    accepted_lock_id: str,
    generator_revision: str,
) -> ModelRecord:
    """Build canonical normalized-model v2 from a verified KEX acquisition lock."""
    if not offline:
        raise KexModelError("KEX model normalization requires --offline")
    lock_payload = _load_json(lock)
    verify_kex_acquisition_lock(lock=lock_payload, cache_dir=cache_dir, offline=True)
    if lock_payload["lock_id"] != accepted_lock_id:
        raise KexModelError(
            f"KEX lock is not the accepted production contract: {lock_payload['lock_id']}"
        )
    inputs = _KexInputs(lock_payload, Path(cache_dir))
    if not re.fullmatch(r"[0-9a-f]{40}", generator_revision):
        raise KexModelError("generator revision must be an exact lowercase 40-hex Git commit")
    model = _build_kex_model(lock_payload, inputs, generator_revision)
    _validate_source_locators(model, inputs)
    model = _canonicalize_kex_model(model)
    _validate_kex_model(model)
    Path(output).write_bytes(canonical_json_bytes(model))
    return model


def verify_kex_model(*, model: str | Path) -> ModelRecord:
    """Authenticate and semantically validate a normalized-model v2 file."""
    payload = _load_json(model)
    _validate_kex_model(payload)
    return payload


def _build_kex_model(lock: ModelRecord, inputs: _KexInputs, generator_revision: str) -> ModelRecord:
    if "ksdk2_0" not in inputs.variants:
        raise KexModelError("normalized-model v2 requires the locked ksdk2_0 binder")
    variant = "ksdk2_0"
    processor, processor_source = inputs.xml(variant, f"{inputs.part}.xml")
    basic = _required_child(processor, "basic_facts", "processor basic_facts")
    core_root, core_source = inputs.xml(variant, "common/cores_info.xml")
    cores = [
        _attributes_with_source(item, core_source, f"/cores/core[{position}]")
        for position, item in enumerate(core_root.findall("core"))
    ]
    package_specs = _package_specs(inputs, variant)
    common_sources = _verify_package_commonality(inputs, variant, package_specs)
    pin_model_declarations = _pin_model_declarations(inputs, variant, package_specs)
    register_index_path = f"{package_specs[0]['sku']}/registers/registers.xml"
    register_index, register_source = inputs.xml(variant, register_index_path)
    interrupts = _interrupts(inputs, variant, package_specs[0]["sku"])
    peripherals = _peripherals(
        inputs, variant, package_specs[0]["sku"], register_index, register_source
    )
    clock_gates = _clock_gates(
        inputs, variant, package_specs[0]["sku"], register_index, register_source
    )
    packages = [_package(inputs, variant, item) for item in package_specs]
    programming_sequences = _programming_sequences(inputs, variant, package_specs)
    pin_routes = _deduplicate_pin_routes(packages)
    _verify_cross_variant_pin_routes(inputs, package_specs)
    dma_requests = _dma_requests(inputs, variant, package_specs[0]["sku"])
    source_documents = _source_documents(inputs)
    model: ModelRecord = {
        "canonicalization": _CANONICALIZATION,
        "clock_documents": _clock_documents(inputs, variant),
        "clock_gates": clock_gates,
        "device": {
            **dict(basic.attrib),
            "cores": cores,
            "endian": _register_endian(register_index),
            "_source": _with_locator(processor_source, "/processor/basic_facts[0]"),
            "_source_fields": {
                "cores": _with_locator(core_source, "/cores"),
                "endian": [
                    _with_locator(
                        source,
                        "/registers/part_information[0]/part_number[0]",
                    )
                    for source in common_sources["registers"]
                ],
            },
        },
        "dma_requests": dma_requests,
        "fact_provenance": [],
        "generator": {
            "command": "nxp-monkey model normalize-kex --lock <source-lock> --cache "
            "<verified-cache> --offline --accepted-lock-id <sha256-id> "
            "--generator-revision <commit> --output <normalized-model>",
            "name": "nxp-monkey",
            "revision": generator_revision,
            "schema_sha256": _schema_sha256(),
            "version": __version__,
        },
        "input_files": _input_file_dispositions(inputs),
        "interrupts": interrupts,
        "model_id": "sha256:" + "0" * 64,
        "package_commonality": common_sources["records"],
        "packages": packages,
        "peripherals": peripherals,
        "pin_model_declarations": pin_model_declarations,
        "pin_routes": pin_routes,
        "programming_sequences": programming_sequences,
        "provenance": [],
        "resource_documents": _resource_documents(inputs, variant, package_specs),
        "schema_version": "2",
        "source_absent": [],
        "source_documents": source_documents,
        "source_lock_id": lock["lock_id"],
        "variant_comparison": _variant_comparison(inputs),
    }
    model["provenance"] = [*inputs.provenance(), _policy_provenance()]
    return model


def _verify_package_commonality(
    inputs: _KexInputs, variant: str, specs: list[ModelRecord]
) -> dict[str, list[ModelRecord]]:
    sources: dict[str, list[ModelRecord]] = {
        "clockGates": [],
        "dmaRequests": [],
        "interrupts": [],
        "records": [],
        "registers": [],
    }
    register_signature = None
    resource_signatures: dict[str, object] = {}
    definition_signatures: dict[str, object] = {}
    definition_sources: dict[str, list[ModelRecord]] = {}
    package_skus = [spec["sku"] for spec in specs]
    for spec in specs:
        sku = spec["sku"]
        index, index_source = inputs.xml(variant, f"{sku}/registers/registers.xml")
        signature = _register_index_signature(index)
        if register_signature is None:
            register_signature = signature
        elif signature != register_signature:
            raise KexModelError(f"package register index differs semantically for {sku}")
        sources["registers"].append(index_source)
        _verify_register_definitions(
            inputs,
            variant,
            sku,
            index,
            definition_signatures,
            definition_sources,
        )
        _verify_common_resources(inputs, variant, sku, resource_signatures, sources)
    sources["records"].append(
        _commonality_record(
            "register-index",
            "registers.xml",
            register_signature,
            package_skus,
            sources["registers"],
        )
    )
    for link in sorted(definition_signatures):
        sources["records"].append(
            _commonality_record(
                "register-definition",
                link,
                definition_signatures[link],
                package_skus,
                definition_sources[link],
            )
        )
    for name in sorted(resource_signatures):
        sources["records"].append(
            _commonality_record(
                "resource-table",
                name + ".xml",
                resource_signatures[name],
                package_skus,
                sources[name],
            )
        )
    return sources


def _verify_register_definitions(
    inputs: _KexInputs,
    variant: str,
    sku: str,
    index: ET.Element,
    signatures: dict[str, object],
    sources: dict[str, list[ModelRecord]],
) -> None:
    seen = set()
    for peripheral in _required_child(index, "peripherals", "register peripherals"):
        link = peripheral.attrib["link"]
        key = Path(link).name
        if key in seen:
            continue
        seen.add(key)
        relative = posixpath.normpath(f"{sku}/registers/{link}")
        definition, source = inputs.xml(variant, relative)
        signature = _plain_xml(definition)
        if key in signatures and signature != signatures[key]:
            raise KexModelError(
                f"package register definition differs semantically for {sku}/{link}"
            )
        signatures.setdefault(key, signature)
        sources.setdefault(key, []).append(source)


def _verify_common_resources(
    inputs: _KexInputs,
    variant: str,
    sku: str,
    signatures: dict[str, object],
    sources: dict[str, list[ModelRecord]],
) -> None:
    for name in ("clockGates", "dmaRequests", "interrupts"):
        root, source = inputs.xml(variant, f"{sku}/resource_tables/{name}.xml")
        signature = _plain_xml(root)
        if name in signatures and signature != signatures[name]:
            raise KexModelError(f"package resource table {name} differs for {sku}")
        signatures.setdefault(name, signature)
        sources[name].append(source)


def _commonality_record(
    kind: str,
    name: str,
    signature: object,
    skus: list[str],
    sources: list[ModelRecord],
) -> ModelRecord:
    unique_sources = []
    for source in sources:
        if source not in unique_sources:
            unique_sources.append(source)
    return {
        "kind": kind,
        "name": name,
        "package_sources": [
            {"sku": sku, "source_ref": source["ref"]}
            for sku, source in zip(skus, sources, strict=True)
        ],
        "semantic_sha256": "sha256:" + hashlib.sha256(canonical_json_bytes(signature)).hexdigest(),
        "_source": unique_sources,
    }


def _register_index_signature(root: ET.Element) -> object:
    normalized = copy.deepcopy(root)
    for item in normalized.findall("./peripherals/peripheral"):
        item.attrib["link"] = Path(item.attrib["link"]).name
    return _plain_xml(normalized)


def _input_file_dispositions(inputs: _KexInputs) -> list[ModelRecord]:
    result = []
    for path, record in sorted(inputs.records.items()):
        suffix = Path(path).suffix.lower()
        if suffix == ".xml" and _is_supplemental_xml(path):
            disposition = "modeled-source-document"
            reason = "schema/semantics-preserving normalized XML source document"
        elif suffix == ".xml" and "/sdk/sdk_components.xml" in path:
            disposition = "binder-metadata"
            reason = (
                "binder SDK-component metadata; retained in the lock but not hardware authority"
            )
        elif suffix == ".xml":
            disposition = "typed-projection-or-locked-duplicate"
            reason = (
                "facts are carried by a typed/generic projection or this is a locked "
                "package/binder duplicate retained for equivalence checking"
            )
        elif suffix == ".js":
            disposition = "auxiliary-not-executed"
            reason = "binder or code-generation program; locked but never executed"
        elif suffix == ".dsn":
            disposition = "auxiliary-visualization"
            reason = "clock diagram drawing; locked but not interpreted as hardware facts"
        else:
            disposition = "compared-metadata"
            reason = "locked auxiliary metadata used only for cross-variant identity comparison"
        inputs.used.add(path)
        result.append(
            {
                "disposition": disposition,
                "path": path,
                "reason": reason,
                "source_ref": _source_id(record),
                "variant": record["variant"],
            }
        )
    return result


def _source_documents(inputs: _KexInputs) -> list[ModelRecord]:
    result = []
    prefix = f"processors/{inputs.part}/"
    for path, record in sorted(inputs.records.items()):
        if not path.endswith(".xml") or not _is_supplemental_xml(path):
            continue
        variant = record["variant"]
        relative = path.removeprefix(prefix + variant + "/")
        root, source = inputs.xml(variant, relative)
        result.append(
            {
                "path": path,
                "root": _xml_node(root, f"/{_local(root.tag)}"),
                "source_ref": source["ref"],
                "variant": variant,
            }
        )
    return result


def _is_supplemental_xml(path: str) -> bool:
    name = Path(path).name
    return (
        name in {"sdk_features.xml", "part_info.xml", "peripherals_model_info.xml"}
        or "/packages/" in path
        or len(Path(path).parts) == 4
    )


def _package_specs(inputs: _KexInputs, variant: str) -> list[ModelRecord]:
    variant_root = inputs.root / "processors" / inputs.part / variant
    specs = []
    for path in sorted(variant_root.glob(f"{inputs.part}V*.xml")):
        root, source = inputs.xml(variant, path.name)
        links = {item.attrib["type"]: dict(item.attrib) for item in root.findall("db_link")}
        required = {"package", "pins_model", "registers"}
        if not required <= links.keys():
            raise KexModelError(f"part {path.stem} lacks required database links")
        specs.append({"links": links, "sku": root.attrib["id"], "_source": source})
    if not specs:
        raise KexModelError("KEX binder has no orderable package records")
    return specs


def _peripherals(
    inputs: _KexInputs,
    variant: str,
    sku: str,
    index: ET.Element,
    index_source: ModelRecord,
) -> list[ModelRecord]:
    result = []
    definition_cache: dict[str, tuple[ET.Element, ModelRecord]] = {}
    for position, item in enumerate(_required_child(index, "peripherals", "register peripherals")):
        link = item.attrib["link"]
        if link not in definition_cache:
            definition_cache[link] = inputs.xml(variant, f"{sku}/registers/{link}")
        definition, definition_source = definition_cache[link]
        aliases = [dict(alias.attrib) for alias in item.findall("alias")]
        address = _required_child(item, "base_address", f"{item.attrib['name']} address")
        result.append(
            {
                "aliases": aliases,
                "base_address": _number(address.attrib["addr"]),
                "definition_file": f"{sku}/registers/{link}",
                "full_name": item.attrib.get("full_name", ""),
                "name": item.attrib["name"],
                "registers": _registers(definition, definition_source),
                "size": int(item.attrib["size"]),
                "_source": _with_locator(
                    index_source, f"/registers/peripherals[0]/peripheral[{position}]"
                ),
            }
        )
    return result


def _registers(root: ET.Element, source: ModelRecord) -> list[ModelRecord]:
    templates = {
        item.attrib["rid"]: (item, position)
        for position, item in enumerate(root.findall("reg_template"))
    }
    registers = [
        _register(item, [_with_locator(source, f"/peripheral/register[{position}]")])
        for position, item in enumerate(root.findall("register"))
    ]
    for position, instance in enumerate(root.findall("reg_instance")):
        rid = instance.attrib["rid"]
        template_record = templates.get(rid)
        if template_record is None:
            raise KexModelError(f"register instance references unknown template {rid}")
        template, template_position = template_record
        values = instance.attrib["vals"].split()
        expanded = _expand_template(template, values)
        instance_source = _with_locator(source, f"/peripheral/reg_instance[{position}]")
        template_source = _with_locator(source, f"/peripheral/reg_template[{template_position}]")
        register = _register(expanded, [template_source], [instance_source])
        register["template"] = {
            "rid": rid,
            "values": values,
            "_source": instance_source,
        }
        registers.append(register)
    registers.sort(key=lambda item: (item["offset"], item["name"]))
    return registers


def _register(
    item: ET.Element,
    structural_sources: list[ModelRecord],
    contextual_sources: list[ModelRecord] | None = None,
) -> ModelRecord:
    context = [] if contextual_sources is None else contextual_sources
    fields = []
    for child in item:
        tag = _local(child.tag)
        if tag not in {"bit_field", "bit_field_array", "reserved_bit_field"}:
            continue
        fields.append(_field(child))
    reset_value, reset_mask = _register_reset(fields)
    return {
        "aliases": [dict(alias.attrib) for alias in item.findall("alias")],
        "description": item.attrib.get("description", ""),
        "fields": fields,
        "name": item.attrib["name"],
        "offset": _number(item.attrib["offset"]),
        "reset_mask": reset_mask,
        "reset_value": reset_value,
        "width": int(item.attrib["width"]),
        "_source": structural_sources + context,
    }


def _field(item: ET.Element) -> ModelRecord:
    offset = _number(item.attrib["offset"])
    width = int(item.attrib["width"])
    reserved = _local(item.tag) == "reserved_bit_field"
    values = []
    for enum in item.findall("bit_field_value"):
        raw = enum.attrib["value"]
        values.append(
            {
                "description": enum.attrib.get("description", ""),
                "name": enum.attrib["name"],
                "numeric_value": _number(raw) if _NUMBER.fullmatch(raw) else None,
                "value": raw,
            }
        )
    return {
        "access": item.attrib.get("access"),
        "aliases": [dict(alias.attrib) for alias in item.findall("alias")],
        "description": item.attrib.get("description", ""),
        "item_width": _optional_int(item.attrib.get("item_width")),
        "name": item.attrib.get("name", f"_reserved_{offset}_{width}"),
        "offset": offset,
        "reserved": reserved,
        "reset_mask": _optional_number(item.attrib.get("reset_mask")),
        "reset_value": _optional_number(item.attrib.get("reset_value")),
        "values": values,
        "width": width,
    }


def _register_reset(fields: list[ModelRecord]) -> tuple[int | None, int | None]:
    value = 0
    mask = 0
    known = False
    for field in fields:
        field_value = field["reset_value"]
        if field_value is None:
            continue
        field_mask = field["reset_mask"]
        width_mask = (1 << field["width"]) - 1
        effective_mask = width_mask if field_mask is None else field_mask
        value |= (field_value & effective_mask) << field["offset"]
        mask |= effective_mask << field["offset"]
        known = True
    return (value, mask) if known else (None, None)


def _expand_template(template: ET.Element, values: list[str]) -> ET.Element:
    expanded = copy.deepcopy(template)
    for item in expanded.iter():
        item.attrib = {key: _substitute(value, values) for key, value in item.attrib.items()}
    return expanded


def _substitute(value: str, values: list[str]) -> str:
    def replacement(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(values):
            raise KexModelError(f"register template placeholder {{{index}}} has no value")
        return values[index]

    return re.sub(r"\{(\d+)\}", replacement, value)


def _index_clock_gate(item: ET.Element, source: ModelRecord, position: int) -> ModelRecord:
    control = _required_child(item, "control", "clock gate control")
    return {
        "control": dict(control.attrib),
        "name": item.attrib["name"],
        "peripherals": [{"device": child.attrib["name"]} for child in item.findall("peripheral")],
        "states": [dict(child.attrib) for child in control.findall("state")],
        "_source": _with_locator(source, f"/registers/clock_gates[0]/clock_gate[{position}]"),
    }


def _clock_gates(
    inputs: _KexInputs,
    variant: str,
    sku: str,
    index: ET.Element,
    index_source: ModelRecord,
) -> list[ModelRecord]:
    indexed = {
        gate["name"]: gate
        for gate in (
            _index_clock_gate(item, index_source, position)
            for position, item in enumerate(index.findall("./clock_gates/clock_gate"))
        )
    }
    relative = f"{sku}/resource_tables/clockGates.xml"
    root, source = inputs.xml(variant, relative)
    result = []
    resource_items, array_locator = _data_array_with_locator(root, "cm33_core0")
    for position, item in enumerate(resource_items):
        settings = _settings(item)
        name = settings["name"]
        control_node = item.find("struct[@name='control']")
        if control_node is None:
            raise KexModelError(f"clock gate {name} lacks control settings")
        control_settings = _settings(control_node)
        gate: ModelRecord = {
            "control": {
                "bit_field": control_settings["bitfield"],
                "peripheral": control_settings["peripheral"],
                "register": control_settings["register"],
            },
            "name": name,
            "peripherals": [_settings(child) for child in _named_array(item, "peripherals")],
            "states": [
                {"name": "disabled", "value": control_settings["disabled"]},
                {"name": "enabled", "value": control_settings["enabled"]},
            ],
            "_source": _with_locator(source, f"{array_locator}/struct[{position}]"),
        }
        indexed_gate = indexed.pop(name, None)
        if indexed_gate is not None:
            if _without_private(indexed_gate) != _without_private(gate):
                raise KexModelError(f"clock gate sources disagree for {name}")
            gate["_source"] = [indexed_gate["_source"], gate["_source"]]
        result.append(gate)
    if indexed:
        raise KexModelError(f"resource clock gates omit register-index gates: {sorted(indexed)}")
    return result


def _interrupts(inputs: _KexInputs, variant: str, sku: str) -> ModelRecord:
    relative = f"{sku}/resource_tables/interrupts.xml"
    root, source = inputs.xml(variant, relative)
    priority = None
    priority_array = _data_array(root, "irqPriorityInfo")
    if len(priority_array) == 1:
        priority = int(_settings(priority_array[0])["irqPriorityBits"])
    vectors = []
    resource_items, array_locator = _data_array_with_locator(root, "cm33_core0")
    for position, item in enumerate(resource_items):
        settings = _settings(item)
        devices = [_settings(device) for device in _named_array(item, "devices")]
        nvic = next((device for device in devices if device.get("id") == "NVIC"), None)
        vectors.append(
            {
                "devices": devices,
                "handler": settings["handler_id"],
                "name": settings["id"].removesuffix("_IRQn"),
                "number": int(nvic["channel"]) if nvic and nvic.get("channel") else None,
                "_source": _with_locator(source, f"{array_locator}/struct[{position}]"),
            }
        )
    return {"priority_bits": priority, "vectors": vectors, "_source": source}


def _dma_requests(inputs: _KexInputs, variant: str, sku: str) -> list[ModelRecord]:
    relative = f"{sku}/resource_tables/dmaRequests.xml"
    root, source = inputs.xml(variant, relative)
    result = []
    resource_items, array_locator = _data_array_with_locator(root, "cm33_core0")
    for position, item in enumerate(resource_items):
        settings = _settings(item)
        result.append(
            {
                "controller": settings["dma_dev"],
                "devices": [_settings(device) for device in _named_array(item, "devices")],
                "disabled": settings["id"].endswith("Disabled"),
                "id": settings["id"],
                "label": settings["label"],
                "number": int(settings["requestNumber"]),
                "_source": _with_locator(source, f"{array_locator}/struct[{position}]"),
            }
        )
    return result


def _package(inputs: _KexInputs, variant: str, spec: ModelRecord) -> ModelRecord:
    links = spec["links"]
    package_relative = links["package"]["link"]
    pins_relative = links["pins_model"]["link"]
    package_root, package_source = inputs.xml(variant, package_relative)
    pins_root, pins_source = inputs.xml(variant, pins_relative)
    look = _required_child(package_root, "package_look", "package look")
    layout = _required_child(package_root, "pins_layout", "package pin layout")
    pins = [
        _pin(item, pins_source, position)
        for position, item in enumerate(pins_root.findall("./pins/pin"))
    ]
    counts = {"gpio_pad": 0, "other": 0, "power": 0}
    for pin in pins:
        counts[pin["category"]] += 1
    declared = int(layout.attrib["num_pins"])
    return {
        "declared_physical_count": declared,
        "geometry": {"layout": dict(layout.attrib), "look": dict(look.attrib)},
        "modeled_pin_records": len(pins),
        "package": look.attrib["type_label"],
        "pin_categories": counts,
        "pins": pins,
        "signal_part_information": _sourced_xml_node(
            _required_child(pins_root, "part_information", "signal part information"),
            "/signal_configuration/part_information[0]",
            pins_source,
        ),
        "sku": spec["sku"],
        "unlisted_position_count": declared - len(pins),
        "_source": spec["_source"],
        "_source_fields": {
            "declared_physical_count": package_source,
            "geometry": package_source,
            "modeled_pin_records": pins_source,
            "package": package_source,
            "pin_categories": pins_source,
            "pins": pins_source,
            "unlisted_position_count": [package_source, pins_source],
        },
    }


def _programming_sequences(
    inputs: _KexInputs, variant: str, specs: list[ModelRecord]
) -> list[ModelRecord]:
    result = []
    for spec in specs:
        relative = spec["links"]["peripherals_model"]["link"]
        root, source = inputs.xml(variant, relative)
        sequence = _required_child(root, "configuration_sequence", "configuration sequence")
        steps = []
        for position, item in enumerate(sequence.findall("configuration_step")):
            steps.append(
                {
                    **dict(item.attrib),
                    "_source": _with_locator(
                        source,
                        "/peripherals_model_info/configuration_sequence[0]"
                        f"/configuration_step[{position}]",
                    ),
                }
            )
        result.append({"sku": spec["sku"], "steps": steps, "_source": source})
    return result


def _pin(item: ET.Element, source: ModelRecord, position: int) -> ModelRecord:
    routes = _pin_routes(item, source, position)
    power = item.attrib.get("power_pin", "false") == "true"
    gpio = bool(re.match(r"^P\d+_\d+(?:/|$)", item.attrib.get("name", "")))
    category = "power" if power else "gpio_pad" if gpio else "other"
    locator = f"/signal_configuration/pins[0]/pin[{position}]"
    functional_properties = item.find("functional_properties")
    return {
        "attributes": dict(item.attrib),
        "category": category,
        "description": item.attrib.get("description", ""),
        "label": item.attrib.get("label"),
        "name": item.attrib.get("name", ""),
        "position": item.attrib["coords"],
        "power_pin": power,
        "functional_properties": (
            _xml_node(functional_properties, locator + "/functional_properties[0]")
            if functional_properties is not None
            else None
        ),
        "routes": routes,
        "_source": _with_locator(source, locator),
        "_source_fields": {
            "category": [
                _with_locator(source, locator),
                _policy_source("/pin/category", category),
            ],
            "power_pin": (
                _with_locator(source, locator)
                if "power_pin" in item.attrib
                else _policy_source("/pin/power-pin", False)
            ),
        },
    }


def _pin_routes(item: ET.Element, source: ModelRecord, position: int) -> list[ModelRecord]:
    routes = []
    for route_position, connections in enumerate(item.findall("connections")):
        for connection_position, connection in enumerate(connections.findall("connection")):
            locator = f"/signal_configuration/pins[0]/pin[{position}]/connections[{route_position}]"
            references = [
                {"kind": _local(ref.tag), **dict(ref.attrib)}
                for ref in connection
                if _local(ref.tag).endswith("_ref")
            ]
            connection_locator = locator + f"/connection[{connection_position}]"
            routes.append(
                {
                    "attributes": dict(connection.attrib),
                    "configurations": [
                        _xml_node(
                            configuration,
                            connection_locator + f"/configuration[{index}]",
                        )
                        for index, configuration in enumerate(connection.findall("configuration"))
                    ],
                    "default": connection.attrib.get("default", "false") == "true",
                    "exclusive": connection.attrib.get("exclusive", "true") == "true",
                    "group_attributes": dict(connections.attrib),
                    "mux": _mux_value(connections.attrib.get("package_function", "")),
                    "name": connections.attrib.get("name_part", ""),
                    "package_function": connections.attrib.get("package_function", ""),
                    "references": references,
                    "_source": _with_locator(source, connection_locator),
                    "_source_fields": {
                        "default": (
                            _with_locator(source, connection_locator)
                            if "default" in connection.attrib
                            else _policy_source("/pin-route/default", False)
                        ),
                        "exclusive": (
                            _with_locator(source, connection_locator)
                            if "exclusive" in connection.attrib
                            else _policy_source("/pin-route/exclusive", True)
                        ),
                        "group_attributes": _with_locator(source, locator),
                        "mux": [
                            _with_locator(source, locator),
                            _policy_source(
                                "/pin-route/mux-alt-decimal-transform",
                                _mux_value(connections.attrib.get("package_function", "")),
                            ),
                        ],
                        "name": _with_locator(source, locator),
                        "package_function": _with_locator(source, locator),
                    },
                }
            )
    return routes


def _deduplicate_pin_routes(packages: list[ModelRecord]) -> list[ModelRecord]:
    catalog: dict[str, ModelRecord] = {}
    for package in packages:
        for pin in package["pins"]:
            routes = pin.pop("routes")
            route_sources = [_mapping(route["_source"], "route source") for route in routes]
            if not route_sources:
                route_sources = [_mapping(pin["_source"], "pin source")]
            payload = {"pin_name": pin["name"], "routes": _without_provenance(routes)}
            semantic_payload = {
                "pin_name": pin["name"],
                "routes": _without_private(routes),
            }
            digest = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()[:24]
            route_id = "pin-routes:" + digest
            pin["route_catalog_id"] = route_id
            if route_id not in catalog:
                catalog[route_id] = {"id": route_id, **payload, "_source": route_sources}
            else:
                existing_semantics = {
                    "pin_name": catalog[route_id]["pin_name"],
                    "routes": _without_private(catalog[route_id]["routes"]),
                }
                if existing_semantics != semantic_payload:
                    raise KexModelError(f"pin route catalog hash collision: {route_id}")
                _merge_source_graph(catalog[route_id]["routes"], routes)
                sources = catalog[route_id]["_source"]
                for source in route_sources:
                    if source not in sources:
                        sources.append(source)
    return sorted(catalog.values(), key=lambda item: (item["pin_name"], item["id"]))


def _without_private(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_private(child)
            for key, child in value.items()
            if not key.startswith("_") and key != "locator"
        }
    if isinstance(value, list):
        return [_without_private(child) for child in value]
    return value


def _without_provenance(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_provenance(child)
            for key, child in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_without_provenance(child) for child in value]
    return value


def _merge_source_graph(target: object, source: object) -> None:
    if isinstance(target, dict) and isinstance(source, dict):
        _merge_source_mapping(target, source)
        return
    if isinstance(target, list) and isinstance(source, list):
        _merge_source_list(target, source)


def _merge_source_mapping(target: ModelRecord, source: ModelRecord) -> None:
    if "_source" in source:
        target["_source"] = _merged_sources(target.get("_source", []), source["_source"])
    target_fields = target.setdefault("_source_fields", {})
    for key, declared in source.get("_source_fields", {}).items():
        target_fields[key] = _merged_sources(target_fields.get(key, []), declared)
    for key, child in source.items():
        if not key.startswith("_") and key in target:
            _merge_source_graph(target[key], child)


def _merge_source_list(target: list[object], source: list[object]) -> None:
    if len(target) != len(source):
        raise KexModelError("semantically equal route graphs have different lengths")
    for target_child, source_child in zip(target, source, strict=True):
        _merge_source_graph(target_child, source_child)


def _merged_sources(left: object, right: object) -> list[ModelRecord]:
    result = _source_list(left)
    for item in _source_list(right):
        if item not in result:
            result.append(item)
    return result


def _pin_model_declarations(
    inputs: _KexInputs, variant: str, specs: list[ModelRecord]
) -> list[ModelRecord]:
    result = []
    for spec in specs:
        relative = spec["links"]["pins_model"]["link"]
        root, source = inputs.xml(variant, relative)
        declarations = [
            child for child in root if _local(child.tag) not in {"part_information", "pins"}
        ]
        positions: dict[str, int] = {}
        nodes = []
        for child in declarations:
            name = _local(child.tag)
            position = positions.get(name, 0)
            positions[name] = position + 1
            locator = f"/signal_configuration/{name}[{position}]"
            nodes.append(_sourced_xml_node(child, locator, source))
        result.append({"declarations": nodes, "sku": spec["sku"], "_source": source})
    return result


def _clock_documents(inputs: _KexInputs, variant: str) -> list[ModelRecord]:
    variant_root = inputs.root / "processors" / inputs.part / variant
    paths = list((variant_root / "clocks").glob("*.xml"))
    paths.extend(variant_root.glob(f"{inputs.part}V*/module_clocks.xml"))
    return [
        _generic_document(inputs, variant, path.relative_to(variant_root)) for path in sorted(paths)
    ]


def _resource_documents(
    inputs: _KexInputs, variant: str, specs: list[ModelRecord]
) -> list[ModelRecord]:
    variant_root = inputs.root / "processors" / inputs.part / variant
    excluded = {"clockGates.xml", "dmaRequests.xml", "interrupts.xml"}
    result = []
    for spec in specs:
        root = variant_root / spec["sku"] / "resource_tables"
        result.extend(
            _generic_document(inputs, variant, path.relative_to(variant_root))
            for path in sorted(root.glob("*.xml"))
            if path.name not in excluded
        )
    return result


def _generic_document(inputs: _KexInputs, variant: str, relative: Path) -> ModelRecord:
    root, source = inputs.xml(variant, relative)
    return {
        "path": relative.as_posix(),
        "root": _xml_node(root, f"/{_local(root.tag)}"),
        "source_ref": source["ref"],
    }


def _sourced_xml_node(item: ET.Element, locator: str, source: ModelRecord) -> ModelRecord:
    result = _xml_node(item, locator)
    result["_source"] = _with_locator(source, locator)
    return result


def _xml_node(item: ET.Element, locator: str) -> ModelRecord:
    children = []
    counts: dict[str, int] = {}
    for child in item:
        name = _local(child.tag)
        position = counts.get(name, 0)
        counts[name] = position + 1
        children.append(_xml_node(child, f"{locator}/{name}[{position}]"))
    result: ModelRecord = {
        "attributes": dict(sorted(item.attrib.items())),
        "children": children,
        "locator": locator,
        "name": _local(item.tag),
        "namespace": _namespace(item.tag),
        "text": item.text.strip() if item.text and item.text.strip() else None,
    }
    return result


def _variant_comparison(inputs: _KexInputs) -> list[ModelRecord]:
    if "zephyr3_2" not in inputs.variants:
        return []
    prefix = f"processors/{inputs.part}/"
    left_prefix = prefix + "ksdk2_0/"
    right_prefix = prefix + "zephyr3_2/"
    left = {
        path.removeprefix(left_prefix): record
        for path, record in inputs.records.items()
        if path.startswith(left_prefix)
    }
    right = {
        path.removeprefix(right_prefix): record
        for path, record in inputs.records.items()
        if path.startswith(right_prefix)
    }
    result = []
    for relative in sorted(left.keys() & right.keys()):
        left_record, right_record = left[relative], right[relative]
        result.append(_variant_comparison_record(inputs, relative, left_record, right_record))
        inputs.used.update((left_prefix + relative, right_prefix + relative))
    return result


def _variant_comparison_record(
    inputs: _KexInputs,
    relative: str,
    left: ModelRecord,
    right: ModelRecord,
) -> ModelRecord:
    same = left["sha256"] == right["sha256"]
    is_pin_model = relative.endswith("signal_configuration.xml")
    is_xml = relative.endswith(".xml")
    classification = _variant_classification(same, is_pin_model, is_xml)
    source_values = None
    semantic_status = "byte-identical"
    if not same and is_xml:
        left_root, _ = inputs.xml("ksdk2_0", relative)
        right_root, _ = inputs.xml("zephyr3_2", relative)
        source_values = _xml_value_differences(left_root, right_root)
        semantic_status = (
            "pin-route-programming-equal; all declarations preserved"
            if is_pin_model
            else "not-assumed-equal; both XML values preserved"
        )
    if not same and not is_xml:
        semantic_status = "identity-only; auxiliary content not modeled"
    return {
        "classification": classification,
        "disposition": (
            "byte-identical" if same else "preserve-both; ksdk2_0 selected for typed projection"
        ),
        "evidence": [
            "docs/research/mcxa156-kex-schema-contract-v1.md#variant-and-package-semantics"
        ],
        "ksdk_sha256": left["sha256"],
        "path": relative,
        "same_bytes": same,
        "selected_variant": "ksdk2_0",
        "semantic_status": semantic_status,
        "source_refs": [_source_id(left), _source_id(right)],
        "source_values": source_values,
        "zephyr_sha256": right["sha256"],
    }


def _variant_classification(same: bool, is_pin_model: bool, is_xml: bool) -> str:
    if same:
        return "identical"
    if is_pin_model:
        return "binder-specific-identifiers; pin routes verified equal"
    if is_xml:
        return "binder-capability-metadata"
    return "binder-auxiliary-metadata"


def _xml_value_differences(left: ET.Element, right: ET.Element) -> list[ModelRecord]:
    left_values = _xml_leaf_values(left, f"/{_local(left.tag)}")
    right_values = _xml_leaf_values(right, f"/{_local(right.tag)}")
    return [
        {
            "ksdk2_0": left_values.get(pointer),
            "pointer": pointer,
            "zephyr3_2": right_values.get(pointer),
        }
        for pointer in sorted(left_values.keys() | right_values.keys())
        if left_values.get(pointer) != right_values.get(pointer)
    ]


def _xml_leaf_values(item: ET.Element, locator: str) -> dict[str, object]:
    result: dict[str, object] = {locator + "/$tag": item.tag}
    result.update({locator + "/@" + key: value for key, value in sorted(item.attrib.items())})
    text = item.text.strip() if item.text and item.text.strip() else None
    if text is not None:
        result[locator + "/#text"] = text
    counts: dict[str, int] = {}
    for child in item:
        name = _local(child.tag)
        position = counts.get(name, 0)
        counts[name] = position + 1
        result.update(_xml_leaf_values(child, f"{locator}/{name}[{position}]"))
    return result


def _verify_cross_variant_pin_routes(inputs: _KexInputs, packages: list[ModelRecord]) -> None:
    if "zephyr3_2" not in inputs.variants:
        return
    for spec in packages:
        relative = spec["links"]["pins_model"]["link"]
        left, _ = inputs.xml("ksdk2_0", relative)
        right, _ = inputs.xml("zephyr3_2", relative)
        if _route_signature(left) != _route_signature(right):
            raise KexModelError(f"KSDK and Zephyr pin routes differ for {spec['sku']}")


def _route_signature(root: ET.Element) -> list[object]:
    result = []
    for pin in root.findall("./pins/pin"):
        routes = []
        for group in pin.findall("connections"):
            routes.append(
                (
                    tuple(sorted(group.attrib.items())),
                    tuple(_plain_xml(connection) for connection in group.findall("connection")),
                )
            )
        result.append((pin.attrib.get("coords"), pin.attrib.get("name"), tuple(routes)))
    return result


def _plain_xml(item: ET.Element) -> object:
    return (
        _local(item.tag),
        tuple(sorted(item.attrib.items())),
        item.text.strip() if item.text and item.text.strip() else None,
        tuple(_plain_xml(child) for child in item),
    )


def _canonicalize_kex_model(model: ModelRecord) -> ModelRecord:
    result = model
    result["peripherals"].sort(key=lambda item: (item["base_address"], item["name"]))
    result["clock_gates"].sort(key=lambda item: item["name"])
    result["dma_requests"].sort(key=lambda item: (item["number"], item["id"]))
    result["packages"].sort(key=lambda item: item["sku"])
    result["pin_routes"].sort(key=lambda item: (item["pin_name"], item["id"]))
    result["clock_documents"].sort(key=lambda item: item["path"])
    result["resource_documents"].sort(key=lambda item: item["path"])
    result["source_documents"].sort(key=lambda item: item["path"])
    result["variant_comparison"].sort(key=lambda item: item["path"])
    result["fact_provenance"] = _extract_fact_provenance(result)
    result["provenance"].sort(key=lambda item: item["id"])
    result["model_id"] = _model_id(result)
    return result


def _validate_source_locators(model: ModelRecord, inputs: _KexInputs) -> None:
    records = {_source_id(record): (path, record) for path, record in inputs.records.items()}
    roots: dict[str, ET.Element] = {}
    fingerprints: dict[tuple[str, str], str] = {}
    _walk_model_sources(model, records, inputs, roots, fingerprints)


def _walk_model_sources(
    value: object,
    records: dict[str, tuple[str, ModelRecord]],
    inputs: _KexInputs,
    roots: dict[str, ET.Element],
    fingerprints: dict[tuple[str, str], str],
) -> None:
    if isinstance(value, dict):
        if "_source" in value:
            _validate_declared_sources(value["_source"], records, inputs, roots, fingerprints)
        for declared in value.get("_source_fields", {}).values():
            _validate_declared_sources(declared, records, inputs, roots, fingerprints)
        for child in value.values():
            _walk_model_sources(child, records, inputs, roots, fingerprints)
        return
    if isinstance(value, list):
        for child in value:
            _walk_model_sources(child, records, inputs, roots, fingerprints)


def _validate_declared_sources(
    declared: object,
    records: dict[str, tuple[str, ModelRecord]],
    inputs: _KexInputs,
    roots: dict[str, ET.Element],
    fingerprints: dict[tuple[str, str], str],
) -> None:
    for source in _source_list(declared):
        _validate_source_reference(source, records, inputs, roots, fingerprints)


def _validate_source_reference(
    source: ModelRecord,
    records: dict[str, tuple[str, ModelRecord]],
    inputs: _KexInputs,
    roots: dict[str, ET.Element],
    fingerprints: dict[tuple[str, str], str],
) -> None:
    ref = _text(source.get("ref"), "source.ref")
    locator = _text(source.get("locator"), "source.locator")
    record_entry = records.get(ref)
    if record_entry is None:
        if _valid_policy_source(ref, source):
            return
        raise KexModelError(f"model source references unknown input: {ref}")
    path, record = record_entry
    if locator == "/":
        source["value_sha256"] = "sha256:" + record["sha256"]
        return
    cache_key = (ref, locator)
    if cache_key in fingerprints:
        source["value_sha256"] = fingerprints[cache_key]
        return
    if not path.endswith(".xml"):
        raise KexModelError(f"non-XML source has element locator: {ref}{locator}")
    if path not in roots:
        roots[path] = ET.parse(inputs.root / Path(path)).getroot()
    root = roots[path]
    element = _resolve_xml_locator(root, locator, ref)
    fingerprint = "sha256:" + hashlib.sha256(canonical_json_bytes(_plain_xml(element))).hexdigest()
    fingerprints[cache_key] = fingerprint
    source["value_sha256"] = fingerprint


def _valid_policy_source(ref: str, source: ModelRecord) -> bool:
    return ref == "policy:nxp-monkey:normalized-model-v2" and bool(
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(source.get("value_sha256", "")))
    )


def _resolve_xml_locator(root: ET.Element, locator: str, ref: str) -> ET.Element:
    tokens = locator.removeprefix("/").split("/")
    if not tokens or tokens[0] != _local(root.tag):
        raise KexModelError(f"source locator has wrong XML root: {ref}{locator}")
    current = root
    for token in tokens[1:]:
        match = re.fullmatch(r"([^\[]+)(?:\[([0-9]+)\])?", token)
        if match is None:
            raise KexModelError(f"invalid XML source locator: {ref}{locator}")
        name, raw_position = match.groups()
        position = 0 if raw_position is None else int(raw_position)
        children = [child for child in current if _local(child.tag) == name]
        if position >= len(children):
            raise KexModelError(f"unresolvable XML source locator: {ref}{locator}")
        current = children[position]
    return current


def _extract_fact_provenance(model: ModelRecord) -> list[ModelRecord]:
    result: list[ModelRecord] = []
    for section in _FACT_SECTIONS:
        _strip_sources(model[section], f"/{section}", [], result)
    return sorted(result, key=lambda item: item["pointer"])


def _strip_sources(
    value: object,
    pointer: str,
    inherited: list[ModelRecord],
    result: list[ModelRecord],
) -> None:
    if isinstance(value, dict):
        declared = value.pop("_source", None)
        current = inherited if declared is None else _source_list(declared)
        field_sources = value.pop("_source_fields", {})
        if declared is not None:
            result.append(_fact_source_record(pointer, current))
        for key, child in value.items():
            child_pointer = pointer + "/" + _pointer(key)
            if key in field_sources:
                child_sources = _source_list(field_sources[key])
                result.append(_fact_source_record(child_pointer, child_sources))
            else:
                child_sources = current
            _strip_sources(child, child_pointer, child_sources, result)
        return
    if isinstance(value, list):
        for position, child in enumerate(value):
            _strip_sources(child, f"{pointer}/{position}", inherited, result)
        return
    if not inherited:
        raise KexModelError(f"model fact lacks provenance: {pointer}")


def _fact_source_record(pointer: str, sources: list[ModelRecord]) -> ModelRecord:
    return {
        "coverage": "subtree",
        "pointer": pointer,
        "sources": sorted(
            sources,
            key=lambda item: (item["ref"], item["locator"], item["value_sha256"]),
        ),
    }


def _validate_kex_model(model: ModelRecord) -> None:
    try:
        jsonschema.validate(model, _kex_model_schema())
    except jsonschema.ValidationError as exc:
        raise KexModelError(f"normalized model failed schema v2 validation: {exc.message}") from exc
    if model["model_id"] != _model_id(model):
        raise KexModelError("normalized-model v2 identity mismatch")
    provenance = {item["id"] for item in model["provenance"]}
    pointers = set()
    for fact in model["fact_provenance"]:
        if fact["pointer"] in pointers:
            raise KexModelError(f"duplicate fact provenance pointer: {fact['pointer']}")
        pointers.add(fact["pointer"])
        if any(source["ref"] not in provenance for source in fact["sources"]):
            raise KexModelError(f"fact provenance references unknown source: {fact['pointer']}")
    for section in _FACT_SECTIONS:
        _validate_fact_coverage(model[section], f"/{section}", False, pointers)
    _validate_kex_semantics(model)


def _validate_kex_semantics(model: ModelRecord) -> None:
    if model["generator"]["schema_sha256"] != _schema_sha256():
        raise KexModelError("normalized-model generator schema hash mismatch")

    provenance = _validate_artifact_semantics(model)
    peripherals = _validate_peripheral_semantics(model["peripherals"])

    declaration_sets, declaration_indexes, known_devices = _declaration_context(model, peripherals)
    _validate_gate_semantics(model["clock_gates"], peripherals, known_devices)
    _validate_interrupt_dma_semantics(model, known_devices)
    route_catalog = _unique_values(model["pin_routes"], "id", "pin route catalog")
    for catalog in model["pin_routes"]:
        _validate_route_identity(catalog)

    packages = _unique_values(model["packages"], "sku", "package")
    _validate_commonality(model["package_commonality"], packages, provenance)
    sequences = _unique_values(model["programming_sequences"], "sku", "programming sequence")
    if set(packages) != set(sequences) or set(packages) != set(declaration_sets):
        raise KexModelError("package, programming-sequence, and pin-declaration SKU sets differ")
    _validate_programming_sequences(model["programming_sequences"], peripherals)
    for package in model["packages"]:
        _validate_package(package, sequences, route_catalog, declaration_indexes)
    _validate_route_reachability(model["packages"], route_catalog)


def _validate_artifact_semantics(model: ModelRecord) -> dict[object, ModelRecord]:
    provenance = _unique_values(model["provenance"], "id", "provenance source")
    input_paths = _unique_values(model["input_files"], "path", "input file")
    for item in model["input_files"]:
        _validate_input_binding(item, provenance)
    for section in ("clock_documents", "resource_documents", "source_documents"):
        for document in model[section]:
            _validate_document_reference(document, section, provenance, input_paths)
    for comparison in model["variant_comparison"]:
        _validate_variant_references(comparison, provenance)
    return provenance


def _validate_input_binding(item: ModelRecord, provenance: dict[object, ModelRecord]) -> None:
    source_ref = item["source_ref"]
    _require_reference(source_ref, provenance, f"input file {item['path']}")
    source = provenance[source_ref]
    if source["path"] != item["path"] or source["variant"] != item["variant"]:
        raise KexModelError(f"input file source binding mismatch: {item['path']}")


def _declaration_context(
    model: ModelRecord, peripherals: dict[object, ModelRecord]
) -> tuple[dict[object, ModelRecord], dict[object, ModelRecord], set[object]]:
    declaration_sets = _unique_values(
        model["pin_model_declarations"], "sku", "pin-model declaration set"
    )
    indexes = {sku: _pin_declaration_index(record) for sku, record in declaration_sets.items()}
    known_devices = set(peripherals)
    for index in indexes.values():
        known_devices.update(index["peripherals"])
    return declaration_sets, indexes, known_devices


def _validate_gate_semantics(
    gates: list[ModelRecord],
    peripherals: dict[object, ModelRecord],
    known_devices: set[object],
) -> None:
    _unique_values(gates, "name", "clock gate")
    for gate in gates:
        _validate_gate(gate, peripherals, known_devices)


def _validate_gate(
    gate: ModelRecord,
    peripherals: dict[object, ModelRecord],
    known_devices: set[object],
) -> None:
    control = gate["control"]["peripheral"]
    _require_reference(control, peripherals, f"clock gate {gate['name']} control")
    registers = _unique_values(peripherals[control]["registers"], "name", f"{control} register")
    register_name = gate["control"]["register"]
    _require_reference(register_name, registers, f"clock gate {gate['name']} register")
    fields = {
        field["name"] for field in registers[register_name]["fields"] if not field["reserved"]
    }
    _require_reference(gate["control"]["bit_field"], fields, f"clock gate {gate['name']} bit field")
    for target in gate["peripherals"]:
        _require_reference(target["device"], known_devices, f"clock gate {gate['name']} target")
    states = _unique_values(gate["states"], "name", f"{gate['name']} state")
    if set(states) != {"disabled", "enabled"}:
        raise KexModelError(f"clock gate has invalid states: {gate['name']}")


def _validate_interrupt_dma_semantics(model: ModelRecord, known_devices: set[object]) -> None:
    vectors = model["interrupts"]["vectors"]
    _unique_values(vectors, "name", "interrupt")
    numbers = [item["number"] for item in vectors if item["number"] is not None]
    if len(numbers) != len(set(numbers)):
        raise KexModelError("duplicate external interrupt number")
    for vector in vectors:
        _validate_interrupt_devices(vector, known_devices)
    _unique_values(model["dma_requests"], "id", "DMA request")
    slots = [(item["controller"], item["number"]) for item in model["dma_requests"]]
    if len(slots) != len(set(slots)):
        raise KexModelError("duplicate DMA controller/request number")
    for request in model["dma_requests"]:
        _validate_dma_devices(request, known_devices)


def _validate_interrupt_devices(vector: ModelRecord, known_devices: set[object]) -> None:
    sentinels = {"NVIC", "RESERVED", "SysTick"}
    for device in vector["devices"]:
        if device["id"] not in sentinels:
            _require_reference(device["id"], known_devices, f"interrupt {vector['name']} device")


def _validate_dma_devices(request: ModelRecord, known_devices: set[object]) -> None:
    _require_reference(
        request["controller"], known_devices, f"DMA request {request['id']} controller"
    )
    for device in request["devices"]:
        _require_reference(device["device"], known_devices, f"DMA request {request['id']} device")


def _validate_route_identity(catalog: ModelRecord) -> None:
    payload = {
        "pin_name": catalog["pin_name"],
        "routes": _without_private(catalog["routes"]),
    }
    expected = "pin-routes:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:24]
    if catalog["id"] != expected:
        raise KexModelError(f"pin route catalog identity mismatch: {catalog['id']}")


def _validate_commonality(
    records: list[ModelRecord],
    packages: dict[object, ModelRecord],
    provenance: dict[object, ModelRecord],
) -> None:
    keys = set()
    for record in records:
        key = (record["kind"], record["name"])
        if key in keys:
            raise KexModelError(f"duplicate package commonality record: {key}")
        keys.add(key)
        package_sources = _unique_values(
            record["package_sources"], "sku", f"package commonality {key} package"
        )
        if set(package_sources) != set(packages):
            raise KexModelError(f"incomplete package commonality proof: {key}")
        for source in record["package_sources"]:
            _require_reference(source["source_ref"], provenance, f"package commonality {key}")


def _validate_programming_sequences(
    sequences: list[ModelRecord], peripherals: dict[object, ModelRecord]
) -> None:
    for sequence in sequences:
        _unique_values(sequence["steps"], "id", f"{sequence['sku']} programming step")
        for step in sequence["steps"]:
            _require_reference(step["peripheral"], peripherals, f"programming step {step['id']}")


def _validate_package(
    package: ModelRecord,
    sequences: dict[object, ModelRecord],
    route_catalog: dict[object, ModelRecord],
    declaration_indexes: dict[object, ModelRecord],
) -> None:
    pins = package["pins"]
    sku = package["sku"]
    if package["modeled_pin_records"] != len(pins):
        raise KexModelError(f"modeled pin count mismatch: {sku}")
    if package["declared_physical_count"] - len(pins) != package["unlisted_position_count"]:
        raise KexModelError(f"unlisted pin count mismatch: {sku}")
    if sum(package["pin_categories"].values()) != len(pins):
        raise KexModelError(f"pin category count mismatch: {sku}")
    _unique_values(pins, "position", f"{sku} pin position")
    allowed_steps = {step["id"] for step in sequences[sku]["steps"]}
    for pin in pins:
        _validate_package_pin(pin, sku, allowed_steps, route_catalog, declaration_indexes[sku])


def _validate_package_pin(
    pin: ModelRecord,
    sku: str,
    allowed_steps: set[object],
    route_catalog: dict[object, ModelRecord],
    declaration_index: ModelRecord,
) -> None:
    route_id = pin["route_catalog_id"]
    label = f"{sku}:{pin['position']}"
    _require_reference(route_id, route_catalog, f"pin {label}")
    if route_catalog[route_id]["pin_name"] != pin["name"]:
        raise KexModelError(f"pin route catalog name mismatch: {label}")
    for route in route_catalog[route_id]["routes"]:
        for reference in route["references"]:
            _validate_pin_reference(reference, declaration_index, sku)
        for configuration in route["configurations"]:
            for step_id in _xml_attribute_values(configuration, "configuration_step"):
                _require_reference(step_id, allowed_steps, f"pin configuration {label}")


def _validate_route_reachability(
    packages: list[ModelRecord], route_catalog: dict[object, ModelRecord]
) -> None:
    referenced = {pin["route_catalog_id"] for package in packages for pin in package["pins"]}
    if referenced != set(route_catalog):
        raise KexModelError("pin route catalog contains unreachable records")


def _validate_peripheral_semantics(
    records: list[ModelRecord],
) -> dict[object, ModelRecord]:
    peripherals = _unique_values(records, "name", "peripheral")
    for peripheral in records:
        _unique_values(peripheral["registers"], "name", f"{peripheral['name']} register")
        for register in peripheral["registers"]:
            _validate_register(peripheral, register)
    return peripherals


def _validate_register(peripheral: ModelRecord, register: ModelRecord) -> None:
    label = f"{peripheral['name']}.{register['name']}"
    width = register["width"]
    if register["offset"] + (width + 7) // 8 > peripheral["size"]:
        raise KexModelError(f"register exceeds peripheral range: {label}")
    _validate_optional_width(register, (1 << width) - 1, f"register {label}")
    field_names: set[str] = set()
    for field in register["fields"]:
        _validate_field(field, width, field_names, label)


def _validate_field(
    field: ModelRecord, register_width: int, names: set[str], register_label: str
) -> None:
    label = f"{register_label}.{field['name']}"
    if field["offset"] + field["width"] > register_width:
        raise KexModelError(f"field exceeds register width: {label}")
    if not field["reserved"] and field["name"] in names:
        raise KexModelError(f"duplicate field name: {label}")
    names.add(field["name"])
    limit = (1 << field["width"]) - 1
    _validate_optional_width(field, limit, f"field {label}")
    _unique_values(field["values"], "name", f"{label} enum")
    numeric_values: set[int] = set()
    for enum in field["values"]:
        numeric = enum["numeric_value"]
        if numeric is not None:
            _validate_enum_numeric(numeric, limit, numeric_values, label, enum["name"])


def _validate_optional_width(record: ModelRecord, limit: int, label: str) -> None:
    for key in ("reset_mask", "reset_value"):
        value = record[key]
        if value is not None and value > limit:
            raise KexModelError(f"{label} {key} exceeds width")


def _validate_enum_numeric(
    numeric: int, limit: int, values: set[int], label: str, enum_name: str
) -> None:
    if numeric > limit:
        raise KexModelError(f"enum value exceeds field width: {label}.{enum_name}")
    if numeric in values:
        raise KexModelError(f"duplicate enum numeric value: {label}={numeric}")
    values.add(numeric)


def _validate_document_reference(
    document: ModelRecord,
    section: str,
    provenance: dict[object, ModelRecord],
    input_paths: dict[object, ModelRecord],
) -> None:
    _require_reference(document["source_ref"], provenance, f"{section} {document['path']}")
    source = provenance[document["source_ref"]]
    if section == "source_documents":
        if document["path"] not in input_paths or source["path"] != document["path"]:
            raise KexModelError(f"source document binding mismatch: {document['path']}")
        if source["variant"] != document["variant"]:
            raise KexModelError(f"source document variant mismatch: {document['path']}")
        return
    expected_suffix = f"/{source['variant']}/{document['path']}"
    if source["variant"] != "ksdk2_0" or not source["path"].endswith(expected_suffix):
        raise KexModelError(f"{section} source binding mismatch: {document['path']}")


def _validate_variant_references(
    comparison: ModelRecord, provenance: dict[object, ModelRecord]
) -> None:
    for source_ref in comparison["source_refs"]:
        _require_reference(source_ref, provenance, f"variant comparison {comparison['path']}")
    sources = [provenance[source_ref] for source_ref in comparison["source_refs"]]
    if [source["variant"] for source in sources] != ["ksdk2_0", "zephyr3_2"]:
        raise KexModelError(f"variant comparison source order mismatch: {comparison['path']}")
    if any(
        not source["path"].endswith(f"/{source['variant']}/{comparison['path']}")
        for source in sources
    ):
        raise KexModelError(f"variant comparison source binding mismatch: {comparison['path']}")
    if (
        comparison["ksdk_sha256"] != sources[0]["sha256"]
        or comparison["zephyr_sha256"] != sources[1]["sha256"]
    ):
        raise KexModelError(f"variant comparison hash mismatch: {comparison['path']}")
    if comparison["same_bytes"] and comparison["source_values"] is not None:
        raise KexModelError(
            f"byte-identical variant comparison carries differences: {comparison['path']}"
        )


def _unique_values(records: list[ModelRecord], key: str, label: str) -> dict[object, ModelRecord]:
    result: dict[object, ModelRecord] = {}
    for record in records:
        value = record[key]
        if value in result:
            raise KexModelError(f"duplicate {label}: {value}")
        result[value] = record
    return result


def _require_reference(value: object, available: Container[object], label: str) -> None:
    if value not in available:
        raise KexModelError(f"dangling {label} reference: {value}")


def _xml_attribute_values(node: ModelRecord, name: str) -> list[str]:
    result = []
    attributes = node["attributes"]
    if name in attributes:
        result.append(attributes[name])
    for child in node["children"]:
        result.extend(_xml_attribute_values(child, name))
    return result


def _pin_declaration_index(declaration_set: ModelRecord) -> ModelRecord:
    peripheral_types: dict[str, ModelRecord] = {}
    peripheral_instances: dict[str, str] = {}
    for root in declaration_set["declarations"]:
        if root["name"] == "peripheral_types":
            _index_pin_peripheral_types(root, peripheral_types)
        elif root["name"] == "peripherals":
            _index_pin_peripheral_instances(root, peripheral_instances)
    for peripheral_id, type_id in peripheral_instances.items():
        _require_reference(type_id, peripheral_types, f"pin peripheral {peripheral_id} type")
    return {"peripherals": peripheral_instances, "types": peripheral_types}


def _index_pin_peripheral_types(root: ModelRecord, result: dict[str, ModelRecord]) -> None:
    for item in _xml_named_children(root, "peripheral_type"):
        type_id = item["attributes"]["id"]
        if type_id in result:
            raise KexModelError(f"duplicate pin peripheral type: {type_id}")
        result[type_id] = _pin_type_signals(item, type_id)


def _pin_type_signals(item: ModelRecord, type_id: str) -> ModelRecord:
    result: ModelRecord = {}
    for group in _xml_named_children(item, "peripheral_signals"):
        for signal in _xml_named_children(group, "peripheral_signal"):
            signal_id = signal["attributes"]["id"]
            if signal_id in result:
                raise KexModelError(f"duplicate pin signal: {type_id}.{signal_id}")
            result[signal_id] = {
                channel["attributes"]["id"]
                for channel_group in _xml_named_children(signal, "signal_channels")
                for channel in _xml_named_children(channel_group, "signal_channel")
            }
    return result


def _index_pin_peripheral_instances(root: ModelRecord, result: dict[str, str]) -> None:
    for item in _xml_named_children(root, "peripheral"):
        peripheral_id = item["attributes"]["id"]
        if peripheral_id in result:
            raise KexModelError(f"duplicate pin peripheral: {peripheral_id}")
        result[peripheral_id] = item["attributes"]["peripheral_type"]


def _xml_named_children(node: ModelRecord, name: str) -> list[ModelRecord]:
    return [child for child in node["children"] if child["name"] == name]


def _validate_pin_reference(
    reference: ModelRecord, declaration_index: ModelRecord, sku: str
) -> None:
    if reference["kind"] != "peripheral_signal_ref":
        raise KexModelError(f"unsupported pin reference kind for {sku}: {reference['kind']}")
    peripheral_id = reference["peripheral"]
    instances = declaration_index["peripherals"]
    _require_reference(peripheral_id, instances, f"pin route {sku} peripheral")
    type_id = instances[peripheral_id]
    signals = declaration_index["types"][type_id]
    signal_id = reference["signal"]
    _require_reference(signal_id, signals, f"pin route {sku} signal")
    channel_id = reference.get("channel")
    if channel_id is not None:
        _require_reference(
            channel_id,
            signals[signal_id],
            f"pin route {sku} signal channel",
        )


def _validate_fact_coverage(value: object, pointer: str, covered: bool, pointers: set[str]) -> None:
    covered = covered or pointer in pointers
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_fact_coverage(child, pointer + "/" + _pointer(key), covered, pointers)
        return
    if isinstance(value, list):
        for position, child in enumerate(value):
            _validate_fact_coverage(child, f"{pointer}/{position}", covered, pointers)
        return
    if not covered:
        raise KexModelError(f"model fact lacks subtree provenance: {pointer}")


def _model_id(model: ModelRecord) -> str:
    payload = {key: value for key, value in model.items() if key != "model_id"}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _kex_model_schema() -> ModelRecord:
    resource = files("nxp_monkey").joinpath("schemas/normalized_model.schema.v2.json")
    return _mapping(json.loads(resource.read_text(encoding="utf-8")), "schema")


def _schema_sha256() -> str:
    resource = files("nxp_monkey").joinpath("schemas/normalized_model.schema.v2.json")
    return hashlib.sha256(resource.read_bytes()).hexdigest()


def _policy_provenance() -> ModelRecord:
    return {
        "id": "policy:nxp-monkey:normalized-model-v2",
        "path": "schemas/normalized_model.schema.v2.json",
        "sha256": _schema_sha256(),
        "variant": "policy",
    }


def _policy_source(locator: str, value: object) -> ModelRecord:
    return {
        "locator": locator,
        "ref": "policy:nxp-monkey:normalized-model-v2",
        "value_sha256": "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
    }


def _register_endian(index: ET.Element) -> str:
    part = index.find("./part_information/part_number")
    if part is None or "endian" not in part.attrib:
        raise KexModelError("register index lacks part endianness")
    return part.attrib["endian"]


def _data_array(root: ET.Element, name: str) -> list[ET.Element]:
    return _data_array_with_locator(root, name)[0]


def _data_array_with_locator(root: ET.Element, name: str) -> tuple[list[ET.Element], str]:
    data = root.find("./data")
    if data is None:
        raise KexModelError("resource table lacks data element")
    arrays = data.findall("array")
    array = next((item for item in arrays if item.attrib.get("name") == name), None)
    if array is None:
        raise KexModelError(f"resource table lacks data array {name}")
    position = arrays.index(array)
    return array.findall("struct"), f"/resource_table/data[0]/array[{position}]"


def _named_array(root: ET.Element, name: str) -> list[ET.Element]:
    array = root.find(f"array[@name='{name}']")
    return [] if array is None else array.findall("struct")


def _settings(root: ET.Element) -> dict[str, str]:
    return {item.attrib["name"]: item.attrib.get("value", "") for item in root.findall("setting")}


def _attributes_with_source(item: ET.Element, source: ModelRecord, locator: str) -> ModelRecord:
    return {**dict(item.attrib), "_source": _with_locator(source, locator)}


def _with_locator(source: ModelRecord, locator: str) -> ModelRecord:
    return {"locator": locator, "ref": source["ref"]}


def _source_list(value: object) -> list[ModelRecord]:
    if isinstance(value, list):
        return [_mapping(item, "source") for item in value]
    if isinstance(value, dict):
        return [value]
    return []


def _source_id(record: ModelRecord) -> str:
    return f"kex:{record['variant']}:{record['path']}"


def _mux_value(value: str) -> int | None:
    match = re.fullmatch(r"alt([0-9]+)", value, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _number(value: str) -> int:
    return int(value, 0)


def _optional_number(value: str | None) -> int | None:
    return None if value is None else _number(value)


def _optional_int(value: str | None) -> int | None:
    return None if value is None else int(value)


def _required_child(root: ET.Element, name: str, label: str) -> ET.Element:
    child = root.find(name)
    if child is None:
        raise KexModelError(f"KEX XML lacks {label}")
    return child


def _namespace(tag: str) -> str:
    return tag[1:].partition("}")[0] if tag.startswith("{") else ""


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _load_json(path: str | Path) -> ModelRecord:
    try:
        return _mapping(json.loads(Path(path).read_text(encoding="utf-8")), "lock")
    except (OSError, json.JSONDecodeError) as exc:
        raise KexModelError(f"cannot read KEX acquisition lock: {exc}") from exc


def _mapping(value: object, label: str) -> ModelRecord:
    if not isinstance(value, dict):
        raise KexModelError(f"{label} must be an object")
    return value


def _records(value: object, label: str) -> list[ModelRecord]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise KexModelError(f"{label} must be an object array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise KexModelError(f"{label} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise KexModelError(f"{label} must be a non-empty string array")
    return value
