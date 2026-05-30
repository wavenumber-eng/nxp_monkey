"""Public dataclasses used by ``nxp_monkey``.

These are the durable shape of values returned from public functions. They
are exported through ``nxp_monkey/__init__.py`` and documented in
``docs/design/api/models.html``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ApiVersion:
    """A single tool-version entry as published by the KEX ``npidb`` endpoint.

    Attributes:
        api_id: NXP-internal numeric identifier for the tool version row.
        name: Human-facing version name (for example ``"25.12.10"``). May be
            empty for placeholder rows; check :attr:`stable`.
        version: Published version string. Empty for unpublished rows.
    """

    api_id: int
    name: str
    version: str

    @property
    def stable(self) -> bool:
        """True when this row carries a published version string."""
        return bool(self.version.strip())


@dataclass(frozen=True)
class StorageEntry:
    """A single entry returned by the KEX ``storage`` directory listing."""

    file_id: str
    full_path: str
    directory: bool
    time: int | None
    size: int | None
    compression: str

    @property
    def name(self) -> str:
        """Base name of the entry's :attr:`full_path`."""
        return PurePosixPath(self.full_path).name


@dataclass(frozen=True)
class PartInfo:
    """Information about a single NXP part as known to the index.

    Attributes:
        part: Canonical part identifier as it appears in the KEX storage tree
            (for example ``"MCXA156"``).
        family: Family root the part belongs to (often the same as ``part``
            for current KEX naming).
        version: Last-seen tool version that publishes this part.
        variants: Tuple of SDK variant names for which this part has data
            (for example ``("ksdk2_0", "zephyr3_2")``).
    """

    part: str
    family: str
    version: str
    variants: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    """A single search result.

    Attributes:
        part: The part identifier that matched.
        family: The family root for that part.
        score: Match score in ``[0.0, 1.0]``. Higher is better. The exact
            scoring rule depends on the search mode (literal vs fuzzy) and
            should be treated as opaque beyond ordering.
        matched_field: Which indexed field carried the match
            (``"part"`` or ``"family"``).
    """

    part: str
    family: str
    score: float
    matched_field: str


@dataclass(frozen=True)
class Processor:
    """Top-level processor header parsed from ``<PART>.xml``.

    Mirrors the ``<basic_facts>`` element of the
    ``http://swtools.freescale.net/XSD/processor/2.0`` schema, plus the
    enabled-tools list.

    Attributes:
        part: Processor id (for example ``"MCXA132"``).
        producer: Vendor name as reported by NXP (typically ``"NXP"``).
        family: Family root (for example ``"MCX"``).
        series: Series label (for example ``"MCX MCXA"``). May be empty.
        default_part: Variant id NXP marks as the default package SKU
            (for example ``"MCXA132VLF"``). May be empty.
        target_products: Product programs that consume this processor
            (for example ``("MCUX",)``).
        enabled_tools: MCUXpresso Config Tools tool names enabled for
            this processor (for example ``("Pins", "Clocks", "Peripherals")``).
    """

    part: str
    producer: str
    family: str
    series: str
    default_part: str
    target_products: tuple[str, ...]
    enabled_tools: tuple[str, ...]


@dataclass(frozen=True)
class DbLink:
    """One ``<db_link>`` entry from a per-variant manifest.

    Per-variant XML files (``<PART_VARIANT>.xml``) carry a manifest of
    typed links to every other data file for that variant. ``DbLink``
    captures one such row verbatim.

    Attributes:
        type: Logical kind of the link (for example ``"package"``,
            ``"registers"``, ``"pins_model"``, ``"cores_info"``).
        link: Relative path under the variant root, as written by NXP.
            May also be a sentinel like ``"$COMMON"``.
        format_version: Schema version reported by upstream
            (for example ``"6.0"`` for register XMLs).
        description: Free-text description from NXP. May be empty.
    """

    type: str
    link: str
    format_version: str
    description: str


@dataclass(frozen=True)
class CpuCore:
    """One CPU core entry from ``common/cores_info.xml``.

    Attributes:
        name: Human-facing core name (for example ``"Cortex-M33"``).
        core_id: Stable identifier (for example ``"cm33_core0"``).
        description: Free-text description from NXP. May be empty.
    """

    name: str
    core_id: str
    description: str


@dataclass(frozen=True)
class PackageVariant:
    """One per-package SKU of a processor (e.g. ``MCXA132VFM``).

    Parsed from ``<PART_VARIANT>.xml`` (``part_number/4.0`` schema).
    The ``db_links`` tuple is the verbatim manifest of typed file
    references; ``package`` is a convenience accessor lifted from the
    ``type="package"`` link.

    Attributes:
        variant: Variant identifier (for example ``"MCXA132VFM"``).
        package: Package XML link if present (for example
            ``"packages/QFN32.xml"``). Empty when the manifest does not
            list one.
        db_links: All ``<db_link>`` rows from the manifest.
        root: Absolute path of the variant subdirectory in the cache.
    """

    variant: str
    package: str
    db_links: tuple[DbLink, ...]
    root: Path

    def link_by_type(self, link_type: str) -> DbLink | None:
        """Return the first :class:`DbLink` whose ``type`` matches.

        Returns ``None`` when no link of that type is present.
        """
        for link in self.db_links:
            if link.type == link_type:
                return link
        return None


@dataclass(frozen=True)
class PartDetails:
    """Structured view of one part's KEX data tree.

    Returned by :func:`nxp_monkey.details`. Combines the small "spine"
    XML files (processor header, per-variant manifests, core list) and
    the cache root path so callers can lazily reach the heavier
    payloads (registers, pins, clocks).

    Attributes:
        part: The processor id this view describes.
        variant_id: SDK variant used to populate the cache (for example
            ``"ksdk2_0"``).
        version: Tool version the data was sourced from.
        header: Parsed top-level ``<PART>.xml``.
        cores: CPU cores as parsed from ``common/cores_info.xml``.
        variants: Per-package variants in the order NXP listed them.
        properties: Key/value pairs from ``processor.properties``.
        root: Absolute path of ``<part>/<sdk_variant>/`` in the cache.
        is_application_processor: True for application-processor parts
            (i.MX 8 etc.) where the MCU-class assumptions in the rest
            of the tree do not hold (no ``clocks/`` directory, binary
            payloads dominate).
    """

    part: str
    variant_id: str
    version: str
    header: Processor
    cores: tuple[CpuCore, ...]
    variants: tuple[PackageVariant, ...]
    properties: dict[str, str] = field(default_factory=dict)
    root: Path = field(default_factory=Path)
    is_application_processor: bool = False


@dataclass(frozen=True)
class IndexMeta:
    """Metadata describing a built index database.

    Attributes:
        built_at: ISO-8601 UTC timestamp of the most recent build.
        source_version: KEX tool version the index was built from
            (``"portfolio-latest"`` when built across all families).
        part_count: Number of parts recorded.
        family_count: Number of distinct families recorded.
    """

    built_at: str
    source_version: str
    part_count: int
    family_count: int
