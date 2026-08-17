"""L99: the accepted portable-model exchange contract is fail-closed."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from nxp_monkey.model import canonicalize_model

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "contracts" / "schemas" / "normalized_model.schema.v0.json"
EXAMPLE = REPO / "docs" / "contracts" / "examples" / "normalized_model.example.v0.json"
SOURCE_PROFILE_SCHEMA = REPO / "docs" / "contracts" / "schemas" / "source_profile.schema.v0.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_identity(record: dict[str, Any], id_field: str) -> str:
    projected = {key: value for key, value in record.items() if key != id_field}
    payload = (
        json.dumps(
            projected, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
        ).encode("utf-8")
        + b"\n"
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _normalize_example_order(model: dict[str, Any]) -> dict[str, Any]:
    """Normalize unordered arrays exercised by the synthetic example."""
    normalized = copy.deepcopy(model)
    normalized["source_lock_ids"].sort()
    normalized["fact_provenance"].sort(key=lambda item: item["pointer"])
    normalized["provenance"].sort(key=lambda item: item["id"])

    def sort_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "provenance_refs":
                    child.sort()
                else:
                    sort_refs(child)
        elif isinstance(value, list):
            for child in value:
                sort_refs(child)

    sort_refs(normalized)
    return normalized


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _fact_leaf_pointers(value: Any, pointer: str = "") -> set[str]:
    """Return factual scalar leaves, excluding provenance bookkeeping."""
    if isinstance(value, dict):
        leaves: set[str] = set()
        for key, child in value.items():
            if key == "provenance_refs":
                continue
            leaves |= _fact_leaf_pointers(child, f"{pointer}/{_escape_pointer_token(key)}")
        return leaves
    if isinstance(value, list):
        leaves = set()
        for index, child in enumerate(value):
            leaves |= _fact_leaf_pointers(child, f"{pointer}/{index}")
        return leaves
    return {pointer}


def _assert_rejected(instance: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance, _load(SCHEMA))


def test_normalized_model_schema_is_valid() -> None:
    """The normalized-model schema is a valid Draft 2020-12 schema."""
    jsonschema.Draft202012Validator.check_schema(_load(SCHEMA))


def test_source_profile_schema_is_valid() -> None:
    """The public source resolution profile is a valid Draft 2020-12 schema."""
    jsonschema.Draft202012Validator.check_schema(_load(SOURCE_PROFILE_SCHEMA))


def test_normalized_model_example_validates() -> None:
    """The synthetic contract example validates against model schema v0."""
    jsonschema.validate(_load(EXAMPLE), _load(SCHEMA))


def test_example_identity_is_truthful() -> None:
    """The example model ID is recomputable from the normative projection."""
    example = _load(EXAMPLE)
    assert example["model_id"] == _canonical_identity(example, "model_id")


def test_example_bytes_are_canonical() -> None:
    """The retained example uses the exact canonical JSON byte format."""
    example = _load(EXAMPLE)
    expected = (
        json.dumps(
            example, ensure_ascii=False, indent=2, sort_keys=True, separators=(",", ": ")
        ).encode("utf-8")
        + b"\n"
    )
    assert EXAMPLE.read_bytes() == expected


def test_unordered_array_permutation_has_one_identity() -> None:
    """Schema-declared unordered arrays normalize before identity hashing."""
    example = _load(EXAMPLE)
    permuted = copy.deepcopy(example)
    permuted["fact_provenance"].reverse()
    assert _canonical_identity(
        _normalize_example_order(permuted), "model_id"
    ) == _canonical_identity(_normalize_example_order(example), "model_id")


def test_nested_unordered_array_permutations_have_one_model_id() -> None:
    model = _load(EXAMPLE)
    derivative = model["derivatives"][0]
    second_memory = copy.deepcopy(derivative["memories"][0])
    second_memory.update(address="0x10000000", name="RAM", linker_region="RAM")
    derivative["memories"].append(second_memory)
    derivative["clocks"] = [
        {
            "id": "clock-b",
            "kind": "mux",
            "max_frequency_hz": None,
            "parents": ["z", "a"],
            "provenance_refs": ["fixture.sample"],
            "selector": None,
        },
        {
            "id": "clock-a",
            "kind": "source",
            "max_frequency_hz": 12000000,
            "parents": [],
            "provenance_refs": ["fixture.sample"],
            "selector": None,
        },
    ]
    derivative["global_pins"] = [
        {
            "name": "P0_3",
            "provenance_refs": ["fixture.sample"],
            "signals": [
                {"mux": 3, "name": "B", "provenance_refs": ["fixture.sample"]},
                {"mux": 2, "name": "A", "provenance_refs": ["fixture.sample"]},
            ],
        }
    ]
    second_package = copy.deepcopy(model["packages"][0])
    second_package.update(package="QFN32", sku="MCXSAMPLEQFN32")
    model["packages"].append(second_package)
    model["boards"] = [
        {
            "device": "MCXSAMPLE",
            "id": "board-b",
            "package_sku": "MCXSAMPLEQFN48",
            "provenance_refs": ["fixture.sample"],
            "resources": [
                {
                    "active_level": "low",
                    "name": "green",
                    "pin": "P0_1",
                    "provenance_refs": ["fixture.sample"],
                    "type": "led",
                }
            ],
        },
        {
            "device": "MCXSAMPLE",
            "id": "board-a",
            "package_sku": "MCXSAMPLEQFN48",
            "provenance_refs": ["fixture.sample"],
            "resources": [],
        },
    ]
    permuted = copy.deepcopy(model)
    permuted["derivatives"][0]["memories"].reverse()
    permuted["derivatives"][0]["clocks"].reverse()
    permuted["derivatives"][0]["clocks"][1]["parents"].reverse()
    permuted["derivatives"][0]["global_pins"][0]["signals"].reverse()
    permuted["packages"].reverse()
    permuted["boards"].reverse()
    assert canonicalize_model(model)["model_id"] == canonicalize_model(permuted)["model_id"]


def test_example_has_field_level_provenance_coverage() -> None:
    """Every factual leaf in each model layer is covered exactly once."""
    example = _load(EXAMPLE)
    expected: set[str] = set()
    for layer in ("ip_blocks", "derivatives", "packages", "boards"):
        expected |= _fact_leaf_pointers(example[layer], f"/{layer}")
    entries = example["fact_provenance"]
    actual = {entry["pointer"] for entry in entries}
    assert len(actual) == len(entries)
    assert actual == expected

    provenance_ids = {record["id"] for record in example["provenance"]}
    assert all(set(entry["provenance_refs"]) <= provenance_ids for entry in entries)
    source_locks = set(example["source_lock_ids"])
    assert all(record["source_lock_id"] in source_locks for record in example["provenance"])


def test_field_provenance_is_required() -> None:
    malformed = _load(EXAMPLE)
    del malformed["fact_provenance"]
    _assert_rejected(malformed)


def test_absolute_provenance_path_is_rejected() -> None:
    malformed = _load(EXAMPLE)
    malformed["provenance"][0]["input_path"] = "C:\\private\\payload.xml"
    _assert_rejected(malformed)


def test_kex_provenance_is_rejected_in_v0() -> None:
    malformed = _load(EXAMPLE)
    malformed["provenance"][0]["source_kind"] = "kex"
    _assert_rejected(malformed)


def test_conflict_requires_owner_disposition_evidence_and_source_value() -> None:
    malformed = _load(EXAMPLE)
    malformed["conflicts"] = [
        {
            "classification": "missing",
            "disposition": "Awaiting source owner",
            "evidence": ["review:fixture"],
            "field": "/derivatives/0/priority_bits/value",
            "id": "fixture.missing-owner",
            "sources": [
                {
                    "locator": "synthetic fixture",
                    "provenance_ref": "fixture.sample",
                    "value_sha256": "sha256:" + "0" * 64,
                }
            ],
            "status": "unresolved",
        }
    ]
    _assert_rejected(malformed)


def test_mismatch_requires_two_source_values() -> None:
    malformed = _load(EXAMPLE)
    malformed["conflicts"] = [
        {
            "classification": "mismatch",
            "disposition": "Synthetic negative case",
            "evidence": ["review:fixture"],
            "field": "/derivatives/0/priority_bits/value",
            "id": "fixture.one-source-mismatch",
            "owner": "contract-test",
            "sources": [
                {
                    "locator": "synthetic fixture",
                    "provenance_ref": "fixture.sample",
                    "value": 3,
                    "value_sha256": "sha256:" + "0" * 64,
                }
            ],
            "status": "unresolved",
        }
    ]
    _assert_rejected(malformed)


@pytest.mark.parametrize("device", ["MCXA156SAMPLE", "MCXA266SAMPLE"])
def test_mcxa_shaped_topology_is_representable(device: str) -> None:
    """The v0 types carry the MCXA PAC/Embassy topology categories."""
    shaped = _load(EXAMPLE)
    shaped["derivatives"][0]["device"] = device
    shaped["derivatives"][0]["clocks"] = [
        {
            "id": "lpuart0-fclk",
            "kind": "gate",
            "max_frequency_hz": 48000000,
            "parents": ["fro"],
            "provenance_refs": ["fixture.sample"],
            "selector": "synthetic selector",
        }
    ]
    shaped["derivatives"][0]["resets"] = [
        {
            "active_level": "low",
            "bit": "LPUART0",
            "id": "lpuart0-reset",
            "provenance_refs": ["fixture.sample"],
            "register": "SYSCON.PRESETCTRL",
        }
    ]
    shaped["derivatives"][0]["instances"] = [
        {
            "address": "0x40000000",
            "clock_ids": ["lpuart0-fclk"],
            "gate": {
                "bit": "LPUART0",
                "config": None,
                "enable_register": "SYSCON.AHBCLKCTRL",
                "reset_register": "SYSCON.PRESETCTRL",
            },
            "ip_block_id": "lpuart.synthetic",
            "name": "LPUART0",
            "provenance_refs": ["fixture.sample"],
            "reset_ids": ["lpuart0-reset"],
        }
    ]
    shaped["derivatives"][0]["dma_requests"] = [
        {
            "controller": "DMA0",
            "instance": "LPUART0",
            "mux": "DMAMUX0",
            "name": "LPUART0_RX",
            "number": 1,
            "provenance_refs": ["fixture.sample"],
            "signal": "RX",
        }
    ]
    shaped["ip_blocks"] = [
        {
            "id": "lpuart.synthetic",
            "name": "LPUART",
            "provenance_refs": ["fixture.sample"],
            "registers": [
                {
                    "access": "read-write",
                    "fields": [
                        {
                            "access": "read-write",
                            "bit_offset": 0,
                            "bit_width": 1,
                            "enumerated_values": [
                                {
                                    "name": "enabled",
                                    "provenance_refs": ["fixture.sample"],
                                    "value": 1,
                                }
                            ],
                            "name": "ENABLE",
                            "provenance_refs": ["fixture.sample"],
                        }
                    ],
                    "name": "CTRL",
                    "offset": "0x0",
                    "provenance_refs": ["fixture.sample"],
                    "reset_value": "0x0",
                    "width_bits": 32,
                }
            ],
            "semantic_hash": "sha256:" + "0" * 64,
        }
    ]
    shaped["packages"][0]["device"] = device
    shaped["packages"][0]["pins"] = [
        {
            "feature": "gpio",
            "pad": "P0_3",
            "position": "A1",
            "provenance_refs": ["fixture.sample"],
            "signals": [{"mux": 2, "name": "LPUART0_TX", "provenance_refs": ["fixture.sample"]}],
            "supply": None,
        }
    ]
    shaped["boards"] = [
        {
            "device": device,
            "id": "frdm-mcxa-synthetic",
            "package_sku": shaped["packages"][0]["sku"],
            "provenance_refs": ["fixture.sample"],
            "resources": [
                {
                    "active_level": "low",
                    "name": "green",
                    "pin": "P3_13",
                    "provenance_refs": ["fixture.sample"],
                    "type": "led",
                },
                {
                    "baud": 115200,
                    "data_bits": 8,
                    "flow_control": "none",
                    "instance": "LPUART0",
                    "name": "vcom",
                    "parity": "none",
                    "provenance_refs": ["fixture.sample"],
                    "rx": {"mux": 2, "pin": "P0_2"},
                    "stop_bits": 1,
                    "transport": "debug-probe-vcom",
                    "tx": {"mux": 2, "pin": "P0_3"},
                    "type": "uart",
                },
            ],
        }
    ]
    jsonschema.validate(shaped, _load(SCHEMA))
