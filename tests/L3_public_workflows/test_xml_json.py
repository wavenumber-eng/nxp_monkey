"""Exercise the public ``nxp_monkey.xml_json`` surface."""
from __future__ import annotations

from pathlib import Path

import nxp_monkey


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_xml_file_to_dict_strips_namespace_and_preserves_xmlns(tmp_path):
    """Root namespace declarations land under ``@_xmlns``; tags lose their prefix."""
    xml_path = tmp_path / "sample.xml"
    _write(
        xml_path,
        '<?xml version="1.0"?>'
        '<processor xmlns="http://swtools.freescale.net/XSD/processor/2.0">'
        '<basic_facts part="MCXA156"/></processor>',
    )
    payload = nxp_monkey.xml_file_to_dict(xml_path)
    assert "processor" in payload
    body = payload["processor"]
    assert body["@_xmlns"][""] == "http://swtools.freescale.net/XSD/processor/2.0"
    assert body["basic_facts"]["@part"] == "MCXA156"


def test_xml_file_to_dict_collapses_pure_text_leaves(tmp_path):
    """Pure-text leaves collapse to their string value."""
    xml_path = tmp_path / "leaf.xml"
    _write(xml_path, "<root><name>MCXA156</name></root>")
    payload = nxp_monkey.xml_file_to_dict(xml_path)
    assert payload == {"root": {"name": "MCXA156"}}


def test_mirror_xml_tree_as_json_writes_parallel_tree(tmp_path):
    """Every ``.xml`` under ``xml_root`` yields a sibling ``.json``."""
    xml_root = tmp_path / "xml"
    json_root = tmp_path / "json"
    _write(xml_root / "a" / "one.xml", "<root><n>1</n></root>")
    _write(xml_root / "b" / "two.xml", "<root><n>2</n></root>")

    written = nxp_monkey.mirror_xml_tree_as_json(xml_root, json_root)
    assert written == 2
    assert (json_root / "a" / "one.json").is_file()
    assert (json_root / "b" / "two.json").is_file()


def test_mirror_xml_tree_as_json_honors_include_and_exclude(tmp_path):
    """``include`` filters then ``exclude`` removes."""
    xml_root = tmp_path / "xml"
    json_root = tmp_path / "json"
    _write(xml_root / "registers" / "adc.xml", "<root/>")
    _write(xml_root / "registers" / "uart.xml", "<root/>")
    _write(xml_root / "packages" / "qfn.xml", "<root/>")

    written = nxp_monkey.mirror_xml_tree_as_json(
        xml_root,
        json_root,
        include=["registers/*.xml"],
        exclude=["registers/uart.xml"],
    )
    assert written == 1
    assert (json_root / "registers" / "adc.json").is_file()
    assert not (json_root / "registers" / "uart.json").exists()
    assert not (json_root / "packages" / "qfn.json").exists()


def test_mirror_xml_tree_as_json_invokes_progress_callback(tmp_path):
    """Progress callback is called once before and after each file."""
    xml_root = tmp_path / "xml"
    json_root = tmp_path / "json"
    _write(xml_root / "one.xml", "<root/>")
    _write(xml_root / "two.xml", "<root/>")

    calls: list[tuple[int, int, str]] = []
    nxp_monkey.mirror_xml_tree_as_json(
        xml_root,
        json_root,
        progress_callback=lambda d, t, r: calls.append((d, t, r)),
    )
    assert calls[0] == (0, 2, "")
    assert calls[-1][0] == 2 and calls[-1][1] == 2
