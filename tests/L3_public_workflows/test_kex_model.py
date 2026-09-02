"""Exercise normalized-model v2 generation from a verified KEX lock."""

from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import jsonschema
import nxp_monkey
import pytest
from nxp_monkey import kex_model as kex_model_module
from nxp_monkey.model import ModelRecord
from nxp_monkey.source_lock import canonical_json_bytes


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _refresh_route_ids(model: ModelRecord) -> None:
    replacements = {}
    for catalog in model["pin_routes"]:
        old_id = catalog["id"]
        payload = {
            "pin_name": catalog["pin_name"],
            "routes": kex_model_module._without_private(catalog["routes"]),
        }
        new_id = "pin-routes:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:24]
        catalog["id"] = new_id
        replacements.setdefault(old_id, new_id)
    for package in model["packages"]:
        for pin in package["pins"]:
            pin["route_catalog_id"] = replacements[pin["route_catalog_id"]]


def _append_unreachable_route(model: ModelRecord) -> None:
    catalog = deepcopy(model["pin_routes"][0])
    catalog["pin_name"] = "UNREACHABLE"
    model["pin_routes"].append(catalog)
    provenance = next(
        item for item in model["fact_provenance"] if item["pointer"] == "/pin_routes/0"
    )
    provenance = deepcopy(provenance)
    provenance["pointer"] = "/pin_routes/1"
    model["fact_provenance"].append(provenance)


def _misbind_source_document(model: ModelRecord) -> None:
    document = model["source_documents"][0]
    replacement = next(item for item in model["input_files"] if item["path"] != document["path"])
    document["source_ref"] = replacement["source_ref"]


def _stage_kex_cache(tmp_path: Path) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    version = cache / "kex" / "26.06"
    tree = version / "processors" / "MCUTEST" / "ksdk2_0"
    _write(
        tree,
        "MCUTEST.xml",
        '<p:processor xmlns:p="http://swtools.freescale.net/XSD/processor/2.0">'
        '<basic_facts id="MCUTEST" producer="NXP" family="MCU" series="TEST" '
        'default_part="MCUTESTVFT"/></p:processor>',
    )
    _write(
        tree,
        "common/cores_info.xml",
        '<c:cores xmlns:c="http://swtools.freescale.net/XSD/processor/2.0/">'
        '<core name="Cortex-M33" id="cm33_core0" description="M33"/></c:cores>',
    )
    _write(
        tree,
        "MCUTESTVFT.xml",
        '<p:part_number xmlns:p="http://swtools.freescale.net/XSD/part_number/4.0" '
        'id="MCUTESTVFT">'
        '<db_link type="package" link="packages/QFN1.xml"/>'
        '<db_link type="registers" link="MCUTESTVFT/registers/registers.xml"/>'
        '<db_link type="peripherals_model" '
        'link="MCUTESTVFT/peripherals_model_info.xml"/>'
        '<db_link type="pins_model" link="MCUTESTVFT/signal_configuration.xml"/>'
        "</p:part_number>",
    )
    _write(
        tree,
        "packages/QFN1.xml",
        '<p:package xmlns:p="http://swtools.freescale.net/XSD/package/1.0">'
        '<package_look type="LQFP" type_label="QFN 1" width="1" height="1"/>'
        '<pins_layout pins_num_H="1" pins_num_V="0" num_BGA_columns="0" '
        'num_BGA_rows="0" pin_length="1" pin_width="1" pin1_edge="Top" '
        'pin1_pos="Edge" pin2pin_horiz="1" pin2pin_vert="1" '
        'pin2edge_horiz="1" pin2edge_vert="1" num_pins="1"/>'
        "</p:package>",
    )
    _write(
        tree,
        "MCUTESTVFT/peripherals_model_info.xml",
        '<p:peripherals_model_info xmlns:p="http://swtools.freescale.net/Pins/'
        'PeripheralsModelInfo.xsd"><configuration_sequence>'
        '<configuration_step id="init_PORT0" peripheral="PORT0"/>'
        "</configuration_sequence></p:peripherals_model_info>",
    )
    _write(
        tree,
        "MCUTESTVFT/registers/registers.xml",
        '<r:registers xmlns:r="http://swtools.freescale.net/XSD/registers/6.0" version="6.0">'
        '<part_information><part_number id="MCUTEST" family="MCU" endian="little"/>'
        '</part_information><peripherals><peripheral name="TEST0" full_name="Test" '
        'link="TEST0.xml" size="8"><base_address addr="0x40000000"/>'
        '<alias type="type" value="TEST"/></peripheral>'
        '<peripheral name="MRCC0" full_name="Reset and clock control" '
        'link="MRCC0.xml" size="8"><base_address addr="0x40001000"/></peripheral>'
        '<peripheral name="PORT0" full_name="Port" link="PORT0.xml" size="8">'
        '<base_address addr="0x40002000"/></peripheral>'
        '<peripheral name="DMA0" full_name="DMA" link="DMA0.xml" size="8">'
        '<base_address addr="0x40003000"/></peripheral></peripherals>'
        '<clock_gates><clock_gate name="TEST0"><control peripheral="MRCC0" '
        'register="MRCC_GLB_CC0" bit_field="TEST0"><state name="disabled" value="0"/>'
        '<state name="enabled" value="1"/></control><peripheral name="TEST0"/>'
        "</clock_gate></clock_gates></r:registers>",
    )
    _write(
        tree,
        "MCUTESTVFT/registers/TEST0.xml",
        '<r:peripheral xmlns:r="http://swtools.freescale.net/XSD/registers/6.0/'
        'regsPeripheral.xsd"><reg_template rid="4" offset="{0}" width="32" '
        'name="DATA{1}" description="Data"><alias type="CMSIS" value="DATA[{1}]"/>'
        '<bit_field offset="0" width="1" name="EN" access="RW" reset_value="1">'
        '<bit_field_value name="DISABLED" value="0b0" description="Disabled"/>'
        '</bit_field></reg_template><reg_instance rid="4" vals="0x4 1"/>'
        '<register offset="0" width="32" name="CTRL" description="Control">'
        '<bit_field offset="0" width="1" name="READY" access="RO" reset_value="0"/>'
        "</register></r:peripheral>",
    )
    _write(
        tree,
        "MCUTESTVFT/registers/MRCC0.xml",
        '<r:peripheral xmlns:r="http://swtools.freescale.net/XSD/registers/6.0/'
        'regsPeripheral.xsd"><register offset="0" width="32" name="MRCC_GLB_CC0" '
        'description="Clock control"><bit_field offset="0" width="1" name="TEST0" '
        'access="RW"/></register></r:peripheral>',
    )
    for peripheral in ("PORT0", "DMA0"):
        _write(
            tree,
            f"MCUTESTVFT/registers/{peripheral}.xml",
            '<r:peripheral xmlns:r="http://swtools.freescale.net/XSD/registers/6.0/'
            'regsPeripheral.xsd"><register offset="0" width="32" name="CTRL" '
            'description="Control"/></r:peripheral>',
        )
    _write(
        tree,
        "MCUTESTVFT/resource_tables/interrupts.xml",
        '<r:resource_table xmlns:r="http://swtools.freescale.net/XSD/resource/1.0">'
        '<data><array name="irqPriorityInfo"><struct name="0">'
        '<setting name="irqPriorityBits" value="3"/></struct></array>'
        '<array name="cm33_core0"><struct name="0">'
        '<setting name="id" value="TEST_IRQn"/><setting name="handler_id" '
        'value="TEST_IRQHandler"/><array name="devices"><struct name="0">'
        '<setting name="id" value="NVIC"/><setting name="channel" value="7"/>'
        "</struct></array></struct></array></data></r:resource_table>",
    )
    _write(
        tree,
        "MCUTESTVFT/resource_tables/dmaRequests.xml",
        '<r:resource_table xmlns:r="http://swtools.freescale.net/XSD/resource/1.0">'
        '<data><array name="cm33_core0"><struct name="0">'
        '<setting name="requestNumber" value="9"/><setting name="id" '
        'value="kDma0RequestTestRx"/><setting name="label" value="Test RX"/>'
        '<setting name="dma_dev" value="DMA0"/><array name="devices">'
        '<struct name="0"><setting name="device" value="TEST0"/>'
        '<setting name="function" value="RX"/></struct></array>'
        "</struct></array></data></r:resource_table>",
    )
    _write(
        tree,
        "MCUTESTVFT/resource_tables/clockGates.xml",
        '<r:resource_table xmlns:r="http://swtools.freescale.net/XSD/resource/1.0">'
        '<data><array name="cm33_core0"><struct name="0">'
        '<setting name="name" value="TEST0"/><struct name="control">'
        '<setting name="peripheral" value="MRCC0"/>'
        '<setting name="register" value="MRCC_GLB_CC0"/>'
        '<setting name="bitfield" value="TEST0"/>'
        '<setting name="enabled" value="1"/>'
        '<setting name="disabled" value="0"/></struct>'
        '<array name="peripherals"><struct name="0">'
        '<setting name="device" value="TEST0"/></struct></array>'
        "</struct></array></data></r:resource_table>",
    )
    _write(
        tree,
        "MCUTESTVFT/signal_configuration.xml",
        '<p:signal_configuration xmlns:p="http://swtools.freescale.net/Pins/PinsModel">'
        '<part_information><part_number id="MCUTESTVFT" pins="1">'
        "<package>QFN</package></part_number></part_information>"
        '<peripheral_types><peripheral_type id="TEST" name="Test">'
        '<peripheral_signals><peripheral_signal id="RX" name="RX"/>'
        "</peripheral_signals></peripheral_type></peripheral_types>"
        '<peripherals><peripheral id="TEST0" name="TEST0" '
        'peripheral_type="TEST"/></peripherals><pins>'
        '<pin name="P0_1/TEST0_RX" description="Test pin" coords="1">'
        '<connections name_part="TEST0_RX" package_function="alt2"><connection>'
        '<peripheral_signal_ref signal="RX" peripheral="TEST0"/>'
        '<configuration><assign register="PORT0_PCR1" bit_field="MUX" '
        'bit_field_mask="0xF00" bit_field_value="0x2" configuration_step="init_PORT0"/>'
        "</configuration></connection></connections></pin></pins></p:signal_configuration>",
    )
    archive = version / "_zips" / "MCUTEST_ksdk2_0.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(version).as_posix())
    lock = tmp_path / "lock.json"
    nxp_monkey.create_kex_acquisition_lock(
        part="MCUTEST",
        upstream_version="26.06",
        variants=["ksdk2_0"],
        cache_dir=cache,
        output=lock,
        producer_revision="a" * 40,
    )
    return cache, lock


def test_kex_model_v2_normalizes_complete_synthetic_graph(tmp_path):
    """Register templates, IRQ, DMA, package routes, and provenance survive."""
    cache, lock = _stage_kex_cache(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    accepted_lock_id = json.loads(lock.read_text(encoding="utf-8"))["lock_id"]
    model = nxp_monkey.normalize_kex_model(
        lock=lock,
        cache_dir=cache,
        output=first,
        offline=True,
        accepted_lock_id=accepted_lock_id,
        generator_revision="b" * 40,
    )
    nxp_monkey.normalize_kex_model(
        lock=lock,
        cache_dir=cache,
        output=second,
        offline=True,
        accepted_lock_id=accepted_lock_id,
        generator_revision="b" * 40,
    )
    assert first.read_bytes() == second.read_bytes()
    assert nxp_monkey.verify_kex_model(model=first)["model_id"] == model["model_id"]
    tampered = deepcopy(model)
    tampered["device"]["family"] = "tampered"
    stale = tmp_path / "stale-model-id.json"
    stale.write_bytes(canonical_json_bytes(tampered))
    with pytest.raises(nxp_monkey.KexModelError, match="identity mismatch"):
        nxp_monkey.verify_kex_model(model=stale)
    assert model["schema_version"] == "2"
    assert model["device"]["id"] == "MCUTEST"
    assert [item["name"] for item in model["peripherals"][0]["registers"]] == [
        "CTRL",
        "DATA1",
    ]
    assert model["interrupts"]["priority_bits"] == 3
    assert model["interrupts"]["vectors"][0]["number"] == 7
    assert model["dma_requests"][0]["number"] == 9
    assert model["clock_gates"][0]["name"] == "TEST0"
    assert model["packages"][0]["pin_categories"] == {
        "gpio_pad": 1,
        "other": 0,
        "power": 0,
    }
    assert model["pin_routes"][0]["routes"][0]["mux"] == 2
    assert model["pin_routes"][0]["routes"][0]["default"] is False
    assert model["packages"][0]["signal_part_information"]["name"] == "part_information"
    assert len(model["package_commonality"]) == 8
    template_fact = next(
        item for item in model["fact_provenance"] if item["pointer"] == "/peripherals/0/registers/1"
    )
    assert {source["locator"] for source in template_fact["sources"]} == {
        "/peripheral/reg_instance[0]",
        "/peripheral/reg_template[0]",
    }
    assert all(
        source["value_sha256"].startswith("sha256:")
        for item in model["fact_provenance"]
        for source in item["sources"]
    )

    mutations = [
        (
            lambda item: item["input_files"][0].__setitem__(
                "source_ref", item["input_files"][1]["source_ref"]
            ),
            "input file source binding mismatch",
        ),
        (_misbind_source_document, "source document binding mismatch"),
        (
            lambda item: item["clock_gates"][0]["control"].__setitem__("register", "MISSING"),
            "clock gate TEST0 register",
        ),
        (
            lambda item: item["peripherals"][0]["registers"][0]["fields"][0].__setitem__(
                "offset", 32
            ),
            "field exceeds register width",
        ),
        (
            lambda item: item["dma_requests"][0]["devices"][0].__setitem__("device", "MISSING"),
            "DMA request kDma0RequestTestRx device",
        ),
        (
            lambda item: item["pin_routes"][0]["routes"][0]["references"][0].__setitem__(
                "channel", "0"
            ),
            "signal channel",
        ),
        (
            lambda item: item["packages"][0]["pins"][0].__setitem__("name", "MISMATCH"),
            "catalog name mismatch",
        ),
        (
            lambda item: item["pin_routes"][0]["routes"][0]["configurations"][0]["children"][0][
                "attributes"
            ].__setitem__("configuration_step", "MISSING"),
            "pin configuration",
        ),
        (
            lambda item: item["package_commonality"][0]["package_sources"][0].__setitem__(
                "sku", "MISSING"
            ),
            "incomplete package commonality proof",
        ),
        (_append_unreachable_route, "unreachable records"),
    ]
    for mutate, match in mutations:
        changed = deepcopy(model)
        mutate(changed)
        _refresh_route_ids(changed)
        changed["model_id"] = kex_model_module._model_id(changed)
        try:
            kex_model_module._validate_kex_model(changed)
        except nxp_monkey.KexModelError as exc:
            assert match in str(exc)
        else:
            pytest.fail(f"validator accepted mutation: {match}")


def test_kex_model_v2_requires_offline(tmp_path):
    """Normalization rejects network-enabled operation before reading inputs."""
    with pytest.raises(nxp_monkey.KexModelError, match="requires --offline"):
        nxp_monkey.normalize_kex_model(
            lock=tmp_path / "missing.json",
            cache_dir=tmp_path / "cache",
            output=tmp_path / "model.json",
            offline=False,
            accepted_lock_id="sha256:" + "c" * 64,
            generator_revision="b" * 40,
        )


def test_kex_model_v2_requires_explicitly_accepted_lock(tmp_path):
    """A valid but unaccepted lock cannot become a production model."""
    cache, lock = _stage_kex_cache(tmp_path)
    with pytest.raises(nxp_monkey.KexModelError, match="accepted production contract"):
        nxp_monkey.normalize_kex_model(
            lock=lock,
            cache_dir=cache,
            output=tmp_path / "model.json",
            offline=True,
            accepted_lock_id="sha256:" + "c" * 64,
            generator_revision="b" * 40,
        )


def test_packaged_kex_model_v2_schema_is_valid():
    """The shipped schema is itself a valid JSON Schema 2020-12 document."""
    schema = json.loads(
        files("nxp_monkey")
        .joinpath("schemas/normalized_model.schema.v2.json")
        .read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator.check_schema(schema)
