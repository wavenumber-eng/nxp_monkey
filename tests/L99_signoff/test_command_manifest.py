"""L99: every CLI command has a manifest entry, a design doc, and live argparse wiring."""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
from nxp_monkey._cli import build_parser

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "docs" / "contracts" / "command_manifest.v0.json"
SCHEMA = REPO / "docs" / "contracts" / "schemas" / "command_manifest.schema.v0.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _live_subcommands() -> set[str]:
    parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(choices.keys())
    return names


def test_command_manifest_validates_against_schema():
    """The manifest JSON validates against its own schema."""
    payload = _load_manifest()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def test_command_manifest_matches_live_parser():
    """The manifest lists every live subcommand and no extras."""
    manifest = {entry["name"] for entry in _load_manifest()["commands"]}
    live = _live_subcommands()
    assert manifest == live, (
        "manifest mismatch:\n"
        f"  manifest only: {sorted(manifest - live)}\n"
        f"  live only:     {sorted(live - manifest)}"
    )


@pytest.mark.parametrize("entry", _load_manifest()["commands"])
def test_command_design_doc_exists_with_data_attr(entry):
    """Each manifest command has a design HTML file with a data-command attr."""
    design_path = REPO / entry["design_doc"]
    assert design_path.is_file(), f"missing design doc: {design_path}"
    text = design_path.read_text(encoding="utf-8")
    assert re.search(
        rf'data-command="{re.escape(entry["name"])}"', text
    ), f"design doc {design_path} is missing data-command=\"{entry['name']}\""
