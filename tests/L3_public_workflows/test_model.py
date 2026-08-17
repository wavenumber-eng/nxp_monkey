from __future__ import annotations

# ruff: noqa: E501
import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import nxp_monkey.model as model_module
import pytest
from nxp_monkey import ModelError, compare_models, normalize_model, validate_model_semantics
from nxp_monkey.nxp_monkey_cmd_model import run_compare

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs" / "contracts" / "examples" / "normalized_model.example.v1.json"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _refresh_identity(model: dict) -> None:
    projection = {key: value for key, value in model.items() if key != "model_id"}
    payload = (
        json.dumps(projection, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode()
    model["model_id"] = "sha256:" + hashlib.sha256(payload).hexdigest()


def test_example_passes_runtime_semantic_validation() -> None:
    validate_model_semantics(_example())


def test_runtime_validation_rejects_dangling_fact_provenance() -> None:
    malformed = _example()
    malformed["fact_provenance"][0]["provenance_refs"] = ["missing.source"]
    _refresh_identity(malformed)
    with pytest.raises(ModelError, match="dangling"):
        validate_model_semantics(malformed)


def test_runtime_validation_rejects_valid_but_wrong_fact_provenance() -> None:
    malformed = _example()
    other = copy.deepcopy(malformed["provenance"][0])
    other["id"] = "fixture.other"
    malformed["provenance"].append(other)
    malformed["fact_provenance"][0]["provenance_refs"] = ["fixture.other"]
    _refresh_identity(malformed)
    with pytest.raises(ModelError, match="coverage"):
        validate_model_semantics(malformed)


def test_runtime_validation_rejects_dangling_clock_reference() -> None:
    malformed = _example()
    malformed["ip_blocks"] = [
        {
            "id": "fixture.block",
            "name": "LPUART",
            "provenance_refs": ["fixture.sample"],
            "registers": [],
            "semantic_hash": "sha256:" + "0" * 64,
            "version": "fixture",
        }
    ]
    malformed["derivatives"][0]["instances"] = [
        {
            "address": "0x40000000",
            "clock_ids": ["missing-clock"],
            "gate": None,
            "ip_block_id": "fixture.block",
            "name": "LPUART0",
            "provenance_refs": ["fixture.sample"],
            "reset_ids": [],
        }
    ]
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="missing clock"):
        validate_model_semantics(malformed)


def test_runtime_validation_rejects_duplicate_instance_name() -> None:
    malformed = _example()
    derivative = malformed["derivatives"][0]
    duplicate = {
        "address": "0x40000000",
        "clock_ids": [],
        "gate": None,
        "ip_block_id": "missing",
        "name": "GPIO0",
        "provenance_refs": ["fixture.sample"],
        "reset_ids": [],
    }
    derivative["instances"].append(copy.deepcopy(duplicate))
    duplicate["address"] = "0x40001000"
    derivative["instances"].append(duplicate)
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="duplicate instances name"):
        validate_model_semantics(malformed)


def test_runtime_validation_rejects_duplicate_nested_keys() -> None:
    malformed = _example()
    signal = {"mux": 0, "name": "GPIO0_0", "provenance_refs": ["fixture.sample"]}
    malformed["derivatives"][0]["global_pins"] = [
        {
            "name": "P0_0",
            "provenance_refs": ["fixture.sample"],
            "signals": [signal, copy.deepcopy(signal)],
        }
    ]
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="duplicate global-pin signal"):
        validate_model_semantics(malformed)

    malformed = _example()
    resource = {
        "active_level": "low",
        "name": "green",
        "pin": "P0_0",
        "provenance_refs": ["fixture.sample"],
        "type": "led",
    }
    malformed["boards"] = [
        {
            "device": "MCXSAMPLE",
            "id": "fixture-board",
            "package_sku": "MCXSAMPLEQFN48",
            "provenance_refs": ["fixture.sample"],
            "resources": [resource, copy.deepcopy(resource)],
        }
    ]
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="duplicate board resource"):
        validate_model_semantics(malformed)


def test_runtime_validation_rejects_missing_clock_parent_and_memory_core() -> None:
    malformed = _example()
    derivative = malformed["derivatives"][0]
    derivative["clocks"] = [
        {
            "id": "mux",
            "kind": "mux",
            "max_frequency_hz": None,
            "parents": ["missing"],
            "provenance_refs": ["fixture.sample"],
            "selector": None,
        }
    ]
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="missing parent"):
        validate_model_semantics(malformed)
    malformed = _example()
    malformed["derivatives"][0]["memories"][0]["cores"] = ["missing"]
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="missing core"):
        validate_model_semantics(malformed)


def test_normalization_requires_offline_before_reading_inputs(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="requires --offline"):
        normalize_model(
            lock=tmp_path / "missing-lock.json",
            cache_dir=tmp_path / "cache",
            output=tmp_path / "model.json",
            offline=False,
        )


def test_compare_equal_models_is_canonical(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    report = compare_models(left=EXAMPLE, right=EXAMPLE, output=output)
    assert report["summary"] == {
        "classified": 0,
        "equal": True,
        "equal_facts": 6,
        "total_differences": 0,
        "unclassified": 0,
    }
    assert output.read_bytes().endswith(b"\n")
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_compare_rejects_legacy_v0_without_reinterpretation(tmp_path: Path) -> None:
    legacy = REPO / "docs" / "contracts" / "examples" / "normalized_model.example.v0.json"
    with pytest.raises(ModelError, match="re-normalize"):
        compare_models(left=legacy, right=legacy, output=tmp_path / "report.json")


def test_compare_classifies_portable_model_compatibility(tmp_path: Path) -> None:
    left = _example()
    right = copy.deepcopy(left)
    right["derivatives"][0]["priority_bits"]["value"] = 4
    _refresh_identity(right)
    left_path, right_path = tmp_path / "left.json", tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")
    report = compare_models(left=left_path, right=right_path, output=tmp_path / "report.json")
    assert report["summary"]["unclassified"] == 0
    assert report["findings"][0]["field"] == "/priority_bits"
    assert report["findings"][0]["classification"] == "incompatible"
    assert all(
        source["locators"] == ["synthetic fixture"] for source in report["findings"][0]["sources"]
    )


def test_compare_keeps_adapter_mismatch_unresolved(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.json"
    adapter.write_text(
        json.dumps({"chips": [], "nvic_prio_bits": 4, "peripherals": []}),
        encoding="utf-8",
    )
    report = compare_models(left=EXAMPLE, right=adapter, output=tmp_path / "report.json")
    assert report["summary"]["unclassified"] == 2
    mismatch = next(item for item in report["findings"] if item["field"] == "/priority_bits")
    assert mismatch["classification"] == "mismatch"
    assert mismatch["owner"] == "nxp-pac-and-embassy-board/pac-reconciliation"
    assert mismatch["rule"] == "fixed-reproduction-inventory-v1"
    assert mismatch["sources"][0]["locators"] == ["synthetic fixture"]
    assert mismatch["sources"][1]["oracle_path"] == "adapter.json"
    assert (
        mismatch["sources"][1]["oracle_sha256"] == hashlib.sha256(adapter.read_bytes()).hexdigest()
    )
    args = argparse.Namespace(
        json=False, left=EXAMPLE, right=adapter, output=tmp_path / "cli-report.json"
    )
    assert run_compare(args) == 2


def test_reproduction_inventory_does_not_shrink_with_portable_projection() -> None:
    inventory = model_module._fixed_reproduction_inventory("MCXA266", "metadata")
    assert inventory is not None
    adapter_projection = {key: "oracle-fact" for key in inventory}
    portable_projection = copy.deepcopy(adapter_projection)
    portable_projection.pop("/instances/GPIO0/address")
    keys = model_module._comparison_keys(
        {"schema_version": "1", "derivatives": [{"device": "MCXA266"}]},
        {"_adapter_kind": "metadata"},
        portable_projection,
        adapter_projection,
    )
    assert len(keys) == 50
    assert "/instances/GPIO0/address" in keys


def test_register_map_rule_is_exact_and_startup_slots_are_numbered() -> None:
    field = "/ip/GPIO0/register_map"
    expected = model_module._KNOWN_REGISTER_MAP_DIFFERENCES[field]
    left = {"count": expected[0][0], "sha256": "sha256:" + expected[0][1]}
    right = {"count": expected[1][0], "sha256": "sha256:" + expected[1][1]}
    assert model_module._is_evidence_backed_reproduction_difference(field, left, right)
    right["count"] += 1
    assert not model_module._is_evidence_backed_reproduction_difference(field, left, right)
    device_entries = [f"Reserved{index}_IRQHandler" for index in range(75)]
    device_entries[31] = "LPUART0_IRQHandler"
    device_entries[74] = "GPIO3_IRQHandler"
    startup = "// The vector table.\nvoid (*vectors[])(void) = {\n// Core Level\n"
    startup += "\n".join(["0,"] * 16 + [f"{name}," for name in device_entries])
    startup += "\n};\nLPUART0_IRQHandler, // 999 : decoy outside initializer\n"
    parsed = model_module._startup_interrupts(startup)
    assert parsed["LPUART0"] == 31
    assert parsed["GPIO3"] == 74
    moved = startup.replace("LPUART0_IRQHandler,", "Reserved_IRQHandler,", 1)
    assert "LPUART0" not in model_module._startup_interrupts(moved)


def test_rust_register_signature_covers_width_and_array_shape() -> None:
    source = """impl Registers {
    pub const fn byte(self) -> crate::pac::common::Reg<Byte, crate::pac::common::R> {
        unsafe { crate::pac::common::Reg::from_ptr(self.ptr.wrapping_add(0x0usize) as _) }
    }
    pub const fn half(self) -> crate::pac::common::Reg<u16, crate::pac::common::R> {
        unsafe { crate::pac::common::Reg::from_ptr(self.ptr.wrapping_add(0x8usize) as _) }
    }
    pub const fn words(self, n: usize) -> crate::pac::common::Reg<Word, crate::pac::common::RW> {
        assert!(n < 4usize);
        unsafe { crate::pac::common::Reg::from_ptr(self.ptr.wrapping_add(0x10usize + n * 4usize) as _) }
    }
}
pub struct Byte(pub u8);
pub struct Word(pub u32);
"""
    original = model_module._rust_register_map_signature(source)
    assert original["count"] == 3
    assert (
        model_module._rust_register_map_signature(source.replace("Byte(pub u8)", "Byte(pub u16)"))
        != original
    )
    assert (
        model_module._rust_register_map_signature(source.replace("Reg<u16", "Reg<u32")) != original
    )
    assert (
        model_module._rust_register_map_signature(source.replace("n < 4usize", "n < 3usize"))
        != original
    )
    assert (
        model_module._rust_register_map_signature(source.replace("n * 4usize", "n * 2usize"))
        != original
    )
    with pytest.raises(model_module.ModelError, match="unresolved backing type Byte"):
        model_module._rust_register_map_signature(source.replace("pub struct Byte(pub u8);\n", ""))


def test_dma_controller_identity_is_consumed() -> None:
    rows = model_module._dma(
        "kDma1RequestLPUART0Rx = 21U\nkDma1RequestLPUART0Tx = 22U",
        "fixture.dma",
        "#define FSL_EDMA_SOC_IP_DMA3 (1)",
        "fixture.soc",
    )
    assert {item["controller"] for item in rows} == {"DMA1"}


def test_compare_accepts_generated_nxp_pac_rust(tmp_path: Path) -> None:
    rust = tmp_path / "mod.rs"
    rust.write_text(
        """pub enum Interrupt {
    LPUART0 = 31,
}
pub const NVIC_PRIO_BITS: u8 = 3;
pub const LPUART0: lpuart::Lpuart = unsafe {
    lpuart::Lpuart::from_ptr(0x4009F000 as _)
};
""",
        encoding="utf-8",
    )
    report = compare_models(left=EXAMPLE, right=rust, output=tmp_path / "rust-report.json")
    assert report["summary"]["unclassified"] == 0
    assert report["summary"]["equal"]


def test_synthetic_offline_normalization_exercises_primary_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.invalid/synthetic-mcxa.git"
    startup_entries = ["0"] * (16 + 75)
    startup_entries[16 + 31] = "LPUART0_IRQHandler"
    startup_entries[16 + 74] = "GPIO3_IRQHandler"
    files = {
        "MCXA/MCXA156/chip.yml": "device.hardware_data:\n  contents:\n    devices:\n      - frequency_mhz: 48\n        core:\n          - {name: cm33, type: cm33, fpu: NO_FPU}\n        memory:\n          memoryBlock:\n            - {name: PROGRAM_FLASH, addr: 0, size: 65536, type: Flash, access: RO}\n        part:\n          - {name: MCXA156VLL}\n",
        "svd/MCXA156.xml": "<device><cpu><nvicPrioBits>3</nvicPrioBits></cpu><peripherals>\n<peripheral><name>DMA0</name><baseAddress>0x40080000</baseAddress><registers><register><name>CSR</name><addressOffset>0</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>GPIO3</name><baseAddress>0x40105000</baseAddress><interrupt><name>GPIO3</name><value>74</value></interrupt><registers><register><name>PDOR</name><addressOffset>0x40</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>PORT0</name><baseAddress>0x400BC000</baseAddress><registers><register><name>PCR0</name><addressOffset>0x80</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>LPUART0</name><baseAddress>0x4009F000</baseAddress><interrupt><name>LPUART0</name><value>31</value></interrupt><registers><register><name>CTRL</name><addressOffset>0x18</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>OSTIMER0</name><baseAddress>0x400AD000</baseAddress><registers><register><name>CTRL</name><addressOffset>0</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>MRCC0</name><baseAddress>0x40091000</baseAddress><registers><register><name>CC0</name><addressOffset>0</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n<peripheral><name>SCG0</name><baseAddress>0x4008F000</baseAddress><registers><register><name>CSR</name><addressOffset>0</addressOffset><size>32</size><access>read-write</access><fields/></register></registers></peripheral>\n</peripherals></device>",
        "MCXA/MCXA156/MCXA156_COMMON.h": "#define __NVIC_PRIO_BITS 3U\ntypedef enum IRQn {\nLPUART0_IRQn = 31,\nGPIO3_IRQn = 74\n} IRQn_Type;\n",
        "MCXA/MCXA156/gcc/startup_MCXA156.S": "__Vectors:\n"
        + "\n".join(f".long {entry}" for entry in startup_entries)
        + "\n.size __Vectors\n",
        "MCXA/MCXA156/gcc/MCXA156_flash.ld": "MEMORY {\n m_text (RX) : ORIGIN = 0x0, LENGTH = 0x10000\n}\n",
        "MCXA/MCXA156/gcc/MCXA156_ram.ld": "MEMORY {\n m_data (RW) : ORIGIN = 0x20000000, LENGTH = 0x1000\n}\n",
        "MCXA/MCXA156/drivers/fsl_clock.h": "kCLOCK_GateDMA0 = (0x0U << 16U) | (1U)), /*!< Clock gate name:\nkCLOCK_GateGPIO3 = (0x2U << 16U) | (7U)), /*!< Clock gate name:\nkCLOCK_GatePORT0 = (0x1U << 16U) | (12U)), /*!< Clock gate name:\nkCLOCK_GateLPUART0 = (0x0U << 16U) | (23U)), /*!< Clock gate name:\nkCLOCK_GateOSTIMER0 = (0x1U << 16U) | (1U)), /*!< Clock gate name:\nkFRO12M_to_LPUART0 = 1,\n",
        "MCXA/MCXA156/drivers/fsl_reset.h": "kDMA0_RST_SHIFT_RSTn = (0U << 8U) | (1U)), /*!< reset\nkGPIO3_RST_SHIFT_RSTn = (2U << 8U) | (7U)), /*!< reset\nkPORT0_RST_SHIFT_RSTn = (1U << 8U) | (12U)), /*!< reset\nkLPUART0_RST_SHIFT_RSTn = (0U << 8U) | (23U)), /*!< reset\nkOSTIMER0_RST_SHIFT_RSTn = (1U << 8U) | (1U)), /*!< reset\n",
        "MCXA/MCXA156/variable.cmake": "set(soc_periph periph1)",
        "MCXA/periph1/PERI_DMA.h": "kDma0RequestLPUART0Rx = 21U\nkDma0RequestLPUART0Tx = 22U",
        "MCXA/MCXA156/drivers/fsl_edma_soc.h": "#define FSL_EDMA_SOC_IP_DMA3 (1)",
        "boards/frdmmcxa156/common/pin_mux/pin_mux.c": "/* package_id: MCXA156VLL */\nconst port_pin_config_t DEBUG_RX = { kPORT_MuxAlt2 };\n/* PORT0_2 is configured as LPUART0_RXD */\nconst port_pin_config_t DEBUG_TX = { kPORT_MuxAlt2 };\n/* PORT0_3 is configured as LPUART0_TXD */\nconst port_pin_config_t LED_GREEN = { kPORT_MuxAlt0 };\n/* PORT3_13 is configured as P3_13 */\n",
        "boards/frdmmcxa156/board.h": "#define BOARD_DEBUG_UART_BASEADDR (uint32_t) LPUART0\n#define BOARD_DEBUG_UART_BAUDRATE 115200U\n#define BOARD_LED_GREEN_GPIO GPIO3\n#define BOARD_LED_GREEN_GPIO_PIN 13U\n#define LOGIC_LED_ON 0U\n",
        "boards/frdmmcxa156/clock_config.c": "Configuration BOARD_BootClockFRO12M\nFMU_FCTRL_RWSC(0x0U)\nConfiguration BOARD_BootClockFRO24M\nFMU_FCTRL_RWSC(0x0U)\nConfiguration BOARD_BootClockFRO48M\nFMU_FCTRL_RWSC(0x0U)\nConfiguration BOARD_BootClockFRO64M\nFMU_FCTRL_RWSC(0x1U)\nConfiguration BOARD_BootClockFRO96M\nFMU_FCTRL_RWSC(0x2U)\n",
    }
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=work, check=True)
    for relative, content in files.items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=work, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    url_key = hashlib.sha256(url.removesuffix(".git").lower().encode()).hexdigest()
    bare = tmp_path / "cache" / "source-v0" / "repositories" / f"{url_key}.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    records = []
    projects = {}
    for relative, content in files.items():
        project = (
            "mcux-soc-svd"
            if relative == "svd/MCXA156.xml"
            else "mcu-sdk-examples"
            if relative.startswith("boards/")
            else "mcux-devices-mcx"
        )
        projects[project] = {"name": project, "resolved_commit": commit, "url": url}
        records.append(
            {
                "path": relative,
                "project": project,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    lock = {
        "closures": {"consumed_inputs": records},
        "lock_id": "sha256:" + "1" * 64,
        "projects": list(projects.values()),
        "request": {"device": "MCXA156"},
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(model_module, "verify_source_lock", lambda **_kwargs: lock)
    model = normalize_model(
        lock=lock_path,
        cache_dir=tmp_path / "cache",
        output=tmp_path / "model.json",
        offline=True,
    )
    derivative = model["derivatives"][0]
    assert {item["image"] for item in derivative["linker_regions"]} == {"flash", "ram"}
    assert derivative["global_pin_scope"]["value"] == "board-required-subset"
    assert {item["name"] for item in derivative["global_pins"]} == {"P0_2", "P0_3", "P3_13"}
    assert all(item["max_frequency_hz"] is None for item in derivative["clocks"])
    provenance = {item["input_path"]: item["source_kind"] for item in model["provenance"]}
    assert provenance["MCXA/MCXA156/MCXA156_COMMON.h"] == "cmsis"
    assert provenance["MCXA/MCXA156/drivers/fsl_clock.h"] == "sdk"
    led = next(item for item in model["boards"][0]["resources"] if item["type"] == "led")
    pin = next(item for item in derivative["global_pins"] if item["name"] == led["pin"])
    assert pin["signals"][0]["provenance_refs"] != led["provenance_refs"]
    malformed = copy.deepcopy(model)
    uart = next(item for item in malformed["boards"][0]["resources"] if item["type"] == "uart")
    uart["rx"]["mux"] = 9
    malformed = model_module.canonicalize_model(malformed)
    with pytest.raises(ModelError, match="pin signal or mux"):
        validate_model_semantics(malformed)
