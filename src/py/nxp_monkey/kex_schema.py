"""Instance-derived structural schema inventory for locked KEX XML."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import cast
from xml.etree import ElementTree


def build_xml_schema_shapes(
    inputs: list[tuple[Path, str, str]],
) -> list[dict[str, object]]:
    """Return deterministic qualified paths, attributes, and cardinalities."""
    observations: dict[tuple[str, str], dict[str, object]] = {}
    for source, relative, variant in inputs:
        root = ElementTree.parse(source).getroot()
        _observe_element(root, variant, relative, "", observations)
    return [_finish(record) for _, record in sorted(observations.items())]


def _observe_element(
    element: ElementTree.Element,
    variant: str,
    relative: str,
    parent_path: str,
    observations: dict[tuple[str, str], dict[str, object]],
) -> None:
    qualified = _qualified(element.tag)
    path = f"{parent_path}/{qualified}"
    key = (variant, path)
    if key not in observations:
        observations[key] = {
            "attributes": set(),
            "child_counts": {},
            "files": set(),
            "occurrences": 0,
            "path": path,
            "text_modes": set(),
            "variant": variant,
        }
    record = observations[key]
    attributes = cast(set[str], record["attributes"])
    attributes.update(_qualified(name) for name in element.attrib)
    cast(set[str], record["files"]).add(relative)
    children = Counter(_qualified(child.tag) for child in element)
    _append_child_counts(record, children)
    text = (element.text or "").strip()
    mode = "mixed" if text and len(element) else "value" if text else "none"
    cast(set[str], record["text_modes"]).add(mode)
    record["occurrences"] = cast(int, record["occurrences"]) + 1
    for child in element:
        _observe_element(child, variant, relative, path, observations)


def _append_child_counts(record: dict[str, object], current: Counter[str]) -> None:
    child_counts = cast(dict[str, list[int]], record["child_counts"])
    prior = cast(int, record["occurrences"])
    for name, values in child_counts.items():
        values.append(current.get(name, 0))
    for name, count in current.items():
        if name not in child_counts:
            child_counts[name] = [0] * prior + [count]


def _finish(record: dict[str, object]) -> dict[str, object]:
    child_counts = cast(dict[str, list[int]], record.pop("child_counts"))
    record["attributes"] = sorted(cast(set[str], record["attributes"]))
    record["children"] = [
        {"name": name, "observed_max": max(values), "observed_min": min(values)}
        for name, values in sorted(child_counts.items())
    ]
    record["files"] = sorted(cast(set[str], record["files"]))
    record["text_modes"] = sorted(cast(set[str], record["text_modes"]))
    return record


def _qualified(tag: str) -> str:
    """Return stable ``Q{namespace}local`` notation for a Clark name."""
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return f"Q{{{namespace}}}{local}"
    return f"Q{{}}{tag}"
