"""Per-part roadmap + inferred schema builder.

Walks an unpacked ``processors/<PART>/<VARIANT>/`` cache tree and emits a
structured guide an LLM agent (or human) can read to locate things
without scanning the whole directory. The output mirrors what is
documented narratively in ``docs/research/xml_survey.md``, but is
generated from the actual on-disk tree so it reflects the part at hand.

The roadmap intentionally captures:

- a short ordered "guide" of steps to follow,
- a ``key_files`` map (logical name -> relative path),
- which optional top-level sections (``security/``, ``dcdx/``,
  ``ddr/``, ``mem_validation/``) are present,
- the distinct XML namespaces observed in the tree (the "inferred
  schema" — there are no public XSDs for these),
- per-package variant pointers,
- where codegen scripts live (and whether Zephyr DT codegen is
  available).

Output is a plain ``dict``, ready to feed into
:func:`nxp_monkey.serialization.write_json`.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Optional top-level directories called out in the XML survey.
OPTIONAL_SECTIONS = ("security", "dcdx", "ddr", "mem_validation")

#: Namespaces we recognize, mapped to a short logical label.
_KNOWN_NS_LABELS = {
    "registers": "registers",
    "clocks": "clocks",
    "pinsModel": "pins_model",
    "part_number": "part_number",
    "processor": "processor",
    "dcd": "dcd",
}

_XMLNS_RE = re.compile(r'xmlns(?::([\w.-]+))?\s*=\s*"([^"]+)"')


def build_roadmap(part_root: Path) -> dict:
    """Return a roadmap dict describing the ``part_root`` tree.

    Args:
        part_root: ``processors/<PART>/<VARIANT>/`` directory.

    Returns:
        A JSON-friendly dict. Safe to write directly with
        :func:`nxp_monkey.serialization.write_json`.
    """
    part = part_root.parent.name
    variant = part_root.name

    key_files = _collect_key_files(part_root, part)
    optional = _collect_optional_sections(part_root)
    package_variants = _collect_package_variants(part_root)
    codegen = _collect_codegen(part_root)
    namespaces = _collect_namespaces(part_root)

    return {
        "part": part,
        "variant": variant,
        "root": str(part_root),
        "guide": _build_guide(part, variant, optional, codegen, package_variants),
        "key_files": key_files,
        "optional_sections": optional,
        "package_variants": package_variants,
        "codegen": codegen,
        "xml_namespaces": namespaces,
        "notes": [
            "No public NXP XSDs are hosted; namespaces above are identifiers only.",
            "All variants of the same silicon describe the same hardware; only "
            "output binders differ. ksdk2_0 is canonical for silicon facts.",
            "For richer parsed views, see `nxp-monkey details <part>` or the "
            "`PartDetails` library type.",
        ],
    }


# --- collectors --------------------------------------------------------------


def _collect_key_files(part_root: Path, part: str) -> dict[str, str]:
    """Map logical names to relative file paths present in the tree."""
    candidates = {
        "header": f"{part}.xml",
        "cores_info": "common/cores_info.xml",
        "sdk_features": "common/sdk_features.xml",
        "sdk_components": "sdk/sdk_components.xml",
        "processor_properties": "processor.properties",
        "module_clocks": "module_clocks.xml",  # ksdk2_0 only
    }
    return {
        name: rel
        for name, rel in candidates.items()
        if (part_root / rel).is_file()
    }


def _collect_optional_sections(part_root: Path) -> dict[str, bool]:
    """Note whether each survey-documented optional section exists."""
    return {name: (part_root / name).is_dir() for name in OPTIONAL_SECTIONS}


def _collect_package_variants(part_root: Path) -> list[dict]:
    """List per-package variant directories and their per-variant XML."""
    variants: list[dict] = []
    for child in sorted(part_root.iterdir()):
        if not child.is_dir():
            continue
        manifest = part_root / f"{child.name}.xml"
        if not manifest.is_file():
            continue
        variants.append(
            {
                "variant": child.name,
                "directory": f"{child.name}/",
                "manifest": f"{child.name}.xml",
            }
        )
    return variants


def _collect_codegen(part_root: Path) -> dict[str, object]:
    """Locate codegen-script directories and Zephyr DT codegen entry points."""
    scripts_root = part_root / "scripts"
    codegen: dict[str, object] = {
        "scripts_root": "scripts/" if scripts_root.is_dir() else None,
        "categories": [],
        "zephyr_dt": {},
    }
    if scripts_root.is_dir():
        cats: list[str] = []
        for sub in sorted(scripts_root.iterdir()):
            if sub.is_dir():
                cats.append(sub.name)
        codegen["categories"] = cats

    # Zephyr-specific DT codegen scripts (only present in zephyr3_2 variant).
    zephyr_entry_points = {
        "pins": "scripts/pins/zephyr_pins_print_code.js",
        "defines": "scripts/pins/zephyr_defines_objects.js",
    }
    found: dict[str, str] = {}
    for label, rel in zephyr_entry_points.items():
        if (part_root / rel).is_file():
            found[label] = rel
    codegen["zephyr_dt"] = found
    return codegen


def _collect_namespaces(part_root: Path) -> list[dict[str, str]]:
    """Scan all XML files for ``xmlns`` declarations.

    Returns one entry per distinct (uri, prefix) pair seen, including a
    short ``label`` when the namespace family is recognized and the
    trailing version number when the URI is shaped like the NXP
    ``.../<family>/<version>`` pattern.
    """
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for xml_path in part_root.rglob("*.xml"):
        try:
            head = xml_path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            continue
        for match in _XMLNS_RE.finditer(head):
            prefix = match.group(1) or ""
            uri = match.group(2)
            key = (uri, prefix)
            if key in seen:
                continue
            entry: dict[str, str] = {"prefix": prefix, "uri": uri}
            family, version = _parse_ns_uri(uri)
            if family is not None:
                entry["family"] = family
            if version is not None:
                entry["version"] = version
            label = _KNOWN_NS_LABELS.get(family or "")
            if label is not None:
                entry["label"] = label
            seen[key] = entry
    return sorted(
        seen.values(),
        key=lambda e: (e.get("family", ""), e.get("version", ""), e["prefix"]),
    )


def _parse_ns_uri(uri: str) -> tuple[str | None, str | None]:
    """Pull the trailing ``/<family>/<version>`` segments from a KEX namespace URI."""
    pieces = [p for p in uri.rstrip("/").split("/") if p]
    if len(pieces) < 2:
        return None, None
    family, version = pieces[-2], pieces[-1]
    if re.fullmatch(r"\d+(\.\d+)*", version):
        return family, version
    return None, None


# --- agent guide -------------------------------------------------------------


def _build_guide(
    part: str,
    variant: str,
    optional: dict[str, bool],
    codegen: dict[str, object],
    package_variants: list[dict],
) -> list[str]:
    """Build a short ordered list of steps for an agent reading this tree."""
    steps = [
        f"This directory holds the {variant} binder for {part}. Same silicon "
        "as the other variants under the same processors/<PART>/ folder.",
        f"Start at `{part}.xml` for the processor header (vendor, family, "
        "series, enabled tool plugins, default part variant).",
        "Read `common/cores_info.xml` for the CPU core list. The presence of "
        "any Cortex-A* core means this is an application-class processor.",
    ]
    if package_variants:
        names = ", ".join(v["variant"] for v in package_variants)
        steps.append(
            f"Per-package SKUs live in sibling directories ({names}). Each "
            "has a flat `<VARIANT>.xml` manifest listing typed `db_link` rows "
            "for that SKU's pins, registers, packages, scripts, etc."
        )
    if variant == "ksdk2_0":
        steps.append(
            "ksdk2_0 is the canonical silicon-data binder: registers, pins, "
            "clocks/, packages/, scripts/ for SDK C codegen."
        )
    elif variant == "zephyr3_2":
        steps.append(
            "zephyr3_2 ships Zephyr DT codegen scripts under scripts/pins/ "
            "(see codegen.zephyr_dt). It does NOT ship `.dts/.dtsi` — those "
            "live in upstream Zephyr's NXP HAL."
        )
    elif variant == "i_mx_2_0":
        steps.append(
            "i_mx_2_0 is the i.MX Linux binder. Application-processor parts "
            "carry additional ddr/ and mem_validation/ trees."
        )
    if codegen.get("zephyr_dt"):
        steps.append(
            "Zephyr pinctrl-DT codegen entry points: see codegen.zephyr_dt."
        )
    on = [k for k, present in optional.items() if present]
    if on:
        steps.append(f"Optional sections present in this tree: {', '.join(on)}.")
    steps.append(
        "Distinct XML namespaces observed are listed in xml_namespaces; "
        "no public XSDs exist, so the schema is identifier-only — infer "
        "shape from instances."
    )
    return steps
