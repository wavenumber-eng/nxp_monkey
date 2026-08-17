from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from nxp_monkey import ModelError, compare_models, normalize_model, validate_model_semantics

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs" / "contracts" / "examples" / "normalized_model.example.v0.json"


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
        "total_differences": 0,
        "unclassified": 0,
    }
    assert output.read_bytes().endswith(b"\n")
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_compare_classifies_only_in_without_hiding_mismatch(tmp_path: Path) -> None:
    left = _example()
    right = copy.deepcopy(left)
    right["derivatives"][0]["priority_bits"]["value"] = 4
    left_path, right_path = tmp_path / "left.json", tmp_path / "right.json"
    left_path.write_text(json.dumps(left), encoding="utf-8")
    right_path.write_text(json.dumps(right), encoding="utf-8")
    report = compare_models(left=left_path, right=right_path, output=tmp_path / "report.json")
    assert report["summary"]["unclassified"] == 1
    assert report["findings"][0]["field"] == "/priority_bits"
