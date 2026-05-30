"""Lossless XML -> JSON serialization for the KEX data trees.

Walks a ``processors/<PART>/<VARIANT>/`` tree (or any directory of NXP
KEX XML files) and emits a parallel JSON tree, one ``.json`` per
``.xml``. Designed so an agent that only speaks JSON can reach every
byte of the silicon data without an XML parser.

Conversion convention (xmltodict-style, namespace-stripped tags):

- Element tag -> local name. The original ``xmlns`` declarations are
  preserved on the root element under ``"@_xmlns"`` so the namespace
  family is still recoverable.
- Attribute -> ``"@<name>"`` key (namespaces stripped).
- Mixed text content -> ``"#text"`` key; pure-text leaves collapse to
  their string value.
- Repeated child elements -> ``list``; single children -> ``dict``.
- Empty elements -> ``None``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

#: Per-file progress callback signature.
#:
#: Called once with ``(0, total, "")`` before any file is written, then
#: after each file with ``(done, total, rel_posix_path)``.
ProgressCallback = Callable[[int, int, str], None]

_NS_TAG_RE = re.compile(r"\{[^}]+\}")
_XMLNS_RE = re.compile(r'xmlns(?::([\w.-]+))?\s*=\s*"([^"]+)"')


def _strip_ns(tag: str) -> str:
    """Return the local name of an ElementTree ``{uri}local`` tag."""
    return _NS_TAG_RE.sub("", tag)


def _element_to_obj(elem: ET.Element) -> object:
    """Recursively convert an ElementTree element to a JSON-friendly object."""
    result: dict[str, object] = {}
    for k, v in elem.attrib.items():
        result[f"@{_strip_ns(k)}"] = v
    children_by_tag: dict[str, list[object]] = {}
    for child in elem:
        tag = _strip_ns(child.tag)
        children_by_tag.setdefault(tag, []).append(_element_to_obj(child))
    for tag, items in children_by_tag.items():
        result[tag] = items[0] if len(items) == 1 else items
    text = (elem.text or "").strip()
    if text:
        if result:
            result["#text"] = text
        else:
            return text
    if not result:
        return None
    return result


def _scan_xmlns(text: str) -> dict[str, str]:
    """Return the ``xmlns`` map declared on the source XML's root element."""
    head = text[:8192]
    found: dict[str, str] = {}
    for match in _XMLNS_RE.finditer(head):
        prefix = match.group(1) or ""
        found[prefix] = match.group(2)
    return found


def xml_file_to_dict(path: Path) -> dict:
    """Parse one XML file and return a JSON-friendly dict.

    Args:
        path: Path to an XML file.

    Returns:
        A dict shaped per the module docstring's convention. The root
        element's local name is the sole top-level key, and the value
        contains an ``"@_xmlns"`` map preserving the source namespace
        declarations.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(text)
    root_local = _strip_ns(root.tag)
    body = _element_to_obj(root)
    xmlns = _scan_xmlns(text)
    if isinstance(body, dict):
        body = {"@_xmlns": xmlns, **body} if xmlns else body
    else:
        body = {"@_xmlns": xmlns, "#text": body} if xmlns else {"#text": body}
    return {root_local: body}


def mirror_xml_tree_as_json(
    xml_root: Path,
    json_root: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> int:
    """Walk every ``.xml`` under ``xml_root`` and write a parallel ``.json``.

    Output path mapping: ``<xml_root>/a/b/c.xml`` is converted to
    ``<json_root>/a/b/c.json``. Existing files are overwritten. Files
    that fail to parse are skipped silently (the raw XML is still
    available under ``xml_root``).

    Args:
        xml_root: Source XML tree root.
        json_root: Destination JSON tree root. Created on demand.
        include: Optional list of glob patterns evaluated against the
            ``xml_root``-relative path (forward-slash form). When
            provided and non-empty, only matching files are converted.
        exclude: Optional list of glob patterns evaluated the same way.
            Matching files are skipped (applied after ``include``).
        progress_callback: Optional :data:`ProgressCallback`. Called
            with ``(0, total, "")`` after filtering and before any file
            is written, then with ``(done, total, rel_posix)`` after
            each successful conversion. Parse failures advance neither
            ``done`` nor ``total``.

    Returns:
        Number of JSON files successfully written.
    """
    import fnmatch
    import json

    json_root.mkdir(parents=True, exist_ok=True)
    inc = include or []
    exc = exclude or []

    candidates: list[Path] = []
    for xml_path in xml_root.rglob("*.xml"):
        rel_posix = xml_path.relative_to(xml_root).as_posix()
        if inc and not any(fnmatch.fnmatch(rel_posix, p) for p in inc):
            continue
        if exc and any(fnmatch.fnmatch(rel_posix, p) for p in exc):
            continue
        candidates.append(xml_path)
    total = len(candidates)
    if progress_callback is not None:
        progress_callback(0, total, "")

    written = 0
    for xml_path in candidates:
        rel_posix = xml_path.relative_to(xml_root).as_posix()
        rel = xml_path.relative_to(xml_root).with_suffix(".json")
        out_path = json_root / rel
        try:
            payload = xml_file_to_dict(xml_path)
        except ET.ParseError:
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written += 1
        if progress_callback is not None:
            progress_callback(written, total, rel_posix)
    return written
