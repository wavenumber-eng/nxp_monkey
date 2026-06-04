"""L99: standards exceptions are documented and schema-valid."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parents[2]
EXCEPTIONS = REPO / "docs" / "contracts" / "exceptions.json"
SCHEMA = REPO / "docs" / "contracts" / "exceptions.schema.v0.json"
EXPECTED_IDS = {
    "NXP-LEGACY-SRC-PY",
    "NXP-PY-SIGNOFF-RATCHET",
    "NXP-PYRIGHT-STANDARD",
    "NXP-RESEARCH-XML-SURVEY",
}


def test_standards_exceptions_validate_against_schema():
    """Standards exceptions validate against their local JSON schema."""
    payload = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def test_standards_exceptions_are_explicit():
    """Known standards exceptions are documented by stable ID."""
    payload = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    found = {entry["id"] for entry in payload["exceptions"]}
    assert found == EXPECTED_IDS
