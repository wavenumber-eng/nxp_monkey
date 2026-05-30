"""Exercise the public ``nxp_monkey.build_roadmap`` surface."""
from __future__ import annotations

from pathlib import Path

import nxp_monkey


def _make_part_tree(tmp_path: Path) -> Path:
    """Stage a minimal MCXA156/ksdk2_0 tree with the spine files."""
    part_root = tmp_path / "MCXA156" / "ksdk2_0"
    (part_root / "common").mkdir(parents=True)
    (part_root / "sdk").mkdir()
    (part_root / "packages").mkdir()
    (part_root / "MCXA156VFT").mkdir()

    (part_root / "MCXA156.xml").write_text(
        '<?xml version="1.0"?>'
        '<processor xmlns="http://swtools.freescale.net/XSD/processor/2.0">'
        '<basic_facts part="MCXA156"/></processor>',
        encoding="utf-8",
    )
    (part_root / "common" / "cores_info.xml").write_text(
        '<?xml version="1.0"?><cores/>', encoding="utf-8"
    )
    (part_root / "processor.properties").write_text("k=v\n", encoding="utf-8")
    (part_root / "MCXA156VFT" / "MCXA156VFT.xml").write_text(
        '<?xml version="1.0"?>'
        '<part_number xmlns="http://swtools.freescale.net/XSD/part_number/4.0"/>',
        encoding="utf-8",
    )
    return part_root


def test_build_roadmap_returns_expected_shape(tmp_path):
    """``build_roadmap`` returns the documented top-level keys."""
    part_root = _make_part_tree(tmp_path)
    roadmap = nxp_monkey.build_roadmap(part_root)
    assert roadmap["part"] == "MCXA156"
    assert roadmap["variant"] == "ksdk2_0"
    for key in (
        "guide",
        "key_files",
        "optional_sections",
        "package_variants",
        "codegen",
        "xml_namespaces",
        "notes",
    ):
        assert key in roadmap, f"missing roadmap key: {key}"


def test_build_roadmap_records_namespaces(tmp_path):
    """Distinct ``xmlns`` URIs are surfaced in ``xml_namespaces``."""
    part_root = _make_part_tree(tmp_path)
    uris = {entry["uri"] for entry in nxp_monkey.build_roadmap(part_root)["xml_namespaces"]}
    assert "http://swtools.freescale.net/XSD/processor/2.0" in uris
    assert "http://swtools.freescale.net/XSD/part_number/4.0" in uris
