"""Pure-stdlib XML readers for the KEX per-part data tree spine.

These readers cover the small high-value files that describe the
structure of a part — top-level processor header, per-variant manifest,
core list, and the ``processor.properties`` key=value file. The heavier
payloads (registers, signal configuration, clocks) are deliberately not
covered here; agents reach those via the :class:`~nxp_monkey.PartDetails`
``root`` path and per-variant ``db_links``.

Every reader takes a single :class:`pathlib.Path` and returns either a
dataclass from :mod:`nxp_monkey.models` or a primitive. They never hit
the network and never cache state beyond what XML parsing implies.

See ``docs/research/xml_survey.md`` for the schema universe these
readers target and the namespaces in play.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .kex_client import NxpFetchError
from .models import CpuCore, DbLink, PackageVariant, Processor

_NS_RE = re.compile(r"^\{([^}]+)\}(.+)$")


def _local(tag: str) -> str:
    """Strip an XML namespace prefix from ``tag``."""
    match = _NS_RE.match(tag)
    return match.group(2) if match else tag


def _parse(path: Path) -> ET.Element:
    """Parse ``path`` and return its root element, raising on failure."""
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise NxpFetchError(f"failed to parse {path}: {exc}") from exc


def read_processor_properties(path: Path) -> dict[str, str]:
    """Parse the simple ``processor.properties`` file.

    The file is a tiny ``key=value`` list with ``;`` for comments. Empty
    lines and comments are skipped. Whitespace around keys and values is
    stripped.

    Args:
        path: Path to ``processor.properties``.

    Returns:
        Dict of property keys to string values. Empty when the file is
        absent or contains no parseable lines.
    """
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def read_processor_header(path: Path) -> Processor:
    """Parse the top-level ``<PART>.xml`` ``<processor>`` document.

    Targets the ``http://swtools.freescale.net/XSD/processor/2.0``
    schema. Reads ``<basic_facts>`` attributes and the ``target_products``
    + ``enabled_tools`` lists.

    Args:
        path: Path to ``<PART>.xml``.

    Returns:
        Populated :class:`~nxp_monkey.Processor`.

    Raises:
        NxpFetchError: When the file is unreadable or missing the
            ``basic_facts`` element.
    """
    root = _parse(path)
    facts = None
    products: list[str] = []
    tools: list[str] = []
    for child in root:
        local = _local(child.tag)
        if local == "basic_facts":
            facts = child
        elif local == "target_products":
            products = [
                (sub.text or "").strip()
                for sub in child
                if _local(sub.tag) == "product" and (sub.text or "").strip()
            ]
        elif local == "enabled_tools":
            tools = [
                (sub.text or "").strip()
                for sub in child
                if _local(sub.tag) == "enabled_tool" and (sub.text or "").strip()
            ]

    if facts is None:
        raise NxpFetchError(f"{path}: missing <basic_facts> element")

    return Processor(
        part=facts.attrib.get("id", ""),
        producer=facts.attrib.get("producer", ""),
        family=facts.attrib.get("family", ""),
        series=facts.attrib.get("series", ""),
        default_part=facts.attrib.get("default_part", ""),
        target_products=tuple(products),
        enabled_tools=tuple(tools),
    )


def read_cores_info(path: Path) -> tuple[CpuCore, ...]:
    """Parse ``common/cores_info.xml`` into a tuple of :class:`CpuCore`.

    Targets the ``http://swtools.freescale.net/XSD/processor/2.0`` (and
    the trailing-slash typo variant) schema. Returns an empty tuple if
    the file is absent.

    Args:
        path: Path to ``common/cores_info.xml``.

    Returns:
        Tuple of :class:`~nxp_monkey.CpuCore`, in document order.
    """
    if not path.is_file():
        return ()
    root = _parse(path)
    cores: list[CpuCore] = []
    for child in root:
        if _local(child.tag) != "core":
            continue
        cores.append(
            CpuCore(
                name=child.attrib.get("name", ""),
                core_id=child.attrib.get("id", ""),
                description=child.attrib.get("description", ""),
            )
        )
    return tuple(cores)


def read_part_variant_manifest(path: Path) -> tuple[str, tuple[DbLink, ...]]:
    """Parse a per-variant ``<PART_VARIANT>.xml`` manifest.

    Targets the ``http://swtools.freescale.net/XSD/part_number/4.0``
    schema. Returns the variant id (from the root ``id`` attribute) and
    the full tuple of ``<db_link>`` rows.

    Args:
        path: Path to ``<PART_VARIANT>.xml``.

    Returns:
        ``(variant_id, db_links)`` where ``db_links`` is a tuple of
        :class:`~nxp_monkey.DbLink` in document order.

    Raises:
        NxpFetchError: On XML parse error or missing root ``id`` attr.
    """
    root = _parse(path)
    variant_id = root.attrib.get("id", "")
    if not variant_id:
        raise NxpFetchError(f"{path}: <part_number> is missing id attribute")
    links: list[DbLink] = []
    for child in root:
        if _local(child.tag) != "db_link":
            continue
        links.append(
            DbLink(
                type=child.attrib.get("type", ""),
                link=child.attrib.get("link", ""),
                format_version=child.attrib.get("format_version", ""),
                description=child.attrib.get("description", ""),
            )
        )
    return variant_id, tuple(links)


def find_package_variants(root: Path) -> tuple[PackageVariant, ...]:
    """Discover and parse every per-package variant under ``root``.

    A variant is a directory containing ``part_info.xml`` and having a
    sibling ``<variant>.xml`` manifest at ``root``. The pair is required
    to count as a variant; standalone directories are skipped.

    Args:
        root: Cache root for one part-variant pair, for example
            ``.../processors/MCXA132/ksdk2_0``.

    Returns:
        Tuple of :class:`~nxp_monkey.PackageVariant` in directory order.
    """
    if not root.is_dir():
        return ()
    out: list[PackageVariant] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if not (entry / "part_info.xml").is_file():
            continue
        manifest_path = root / f"{entry.name}.xml"
        if not manifest_path.is_file():
            continue
        variant_id, links = read_part_variant_manifest(manifest_path)
        package_link = ""
        for link in links:
            if link.type == "package":
                package_link = link.link
                break
        out.append(
            PackageVariant(
                variant=variant_id,
                package=package_link,
                db_links=links,
                root=entry,
            )
        )
    return tuple(out)
