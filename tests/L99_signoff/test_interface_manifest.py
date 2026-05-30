"""L99: every public interface has a manifest entry, a design doc, and a docstring."""
from __future__ import annotations

import importlib
import inspect
import json
import re
from pathlib import Path

import jsonschema
import nxp_monkey
import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "docs" / "contracts" / "interface_design_manifest.v0.json"
SCHEMA = REPO / "docs" / "contracts" / "schemas" / "interface_design_manifest.schema.v0.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_interface_manifest_validates_against_schema():
    """The interface manifest validates against its own schema."""
    payload = _load_manifest()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


def test_interface_manifest_covers_public_all():
    """Every name in ``nxp_monkey.__all__`` (except ``__version__`` and the
    plain default-variant constants) is listed in the manifest.
    """
    listed = {entry["symbol"] for entry in _load_manifest()["interfaces"]}
    public = set(nxp_monkey.__all__)
    skip = {"__version__", "DEFAULT_VARIANT", "DEFAULT_SDK_VARIANTS"}
    expected = public - skip
    missing = expected - listed
    assert not missing, f"public symbols missing from interface manifest: {sorted(missing)}"


@pytest.mark.parametrize("entry", _load_manifest()["interfaces"])
def test_interface_design_doc_exists_with_data_attr(entry):
    """Each manifest interface has a design HTML file with a data-interface attr."""
    design_path = REPO / entry["design_doc"]
    assert design_path.is_file(), f"missing design doc: {design_path}"
    text = design_path.read_text(encoding="utf-8")
    assert re.search(
        rf'data-interface="{re.escape(entry["symbol"])}"', text
    ), f"design doc {design_path} is missing data-interface=\"{entry['symbol']}\""


@pytest.mark.parametrize("entry", _load_manifest()["interfaces"])
def test_interface_symbol_resolves_and_has_docstring(entry):
    """Each listed symbol is importable and carries a PEP 257 docstring."""
    module = importlib.import_module(entry["module"])
    assert hasattr(module, entry["symbol"]), (
        f"{entry['module']} has no symbol {entry['symbol']}"
    )
    obj = getattr(module, entry["symbol"])
    doc = inspect.getdoc(obj)
    assert doc and doc.strip(), (
        f"{entry['module']}.{entry['symbol']} is missing a docstring"
    )
