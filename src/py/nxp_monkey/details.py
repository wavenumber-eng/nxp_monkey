"""``details(part)`` — structured view of a part's KEX data tree.

Orchestrates the small spine readers in :mod:`nxp_monkey.readers` and
returns a :class:`~nxp_monkey.PartDetails`. Triggers a :func:`fetch`
when the cache is empty for the requested part.

See ``docs/research/xml_survey.md`` for the schema universe and
``docs/design/api/details.html`` for the contract.
"""
from __future__ import annotations

from . import cache
from .fetch import DEFAULT_VARIANT, fetch
from .kex_client import KexClient, NxpFetchError
from .models import PartDetails
from .readers import (
    find_package_variants,
    read_cores_info,
    read_processor_header,
    read_processor_properties,
)


def details(
    part: str,
    *,
    variant: str = DEFAULT_VARIANT,
    version: str | None = None,
    client: KexClient | None = None,
) -> PartDetails:
    """Return a structured view of ``part``'s data tree.

    Fetches the part on demand if it is not already in the local cache,
    then parses the spine XML files into a :class:`~nxp_monkey.PartDetails`.
    Heavier payloads (per-peripheral registers, signal configuration,
    clocks) are not eagerly parsed; reach them via the returned object's
    ``root`` and per-variant ``db_links``.

    Args:
        part: Processor id (for example ``"MCXA132"``).
        variant: SDK variant to load. Defaults to ``"ksdk2_0"`` per
            ADR-0008.
        version: Optional explicit tool version. ``None`` resolves to
            the portfolio-latest version for the family.
        client: Optional :class:`KexClient` to reuse.

    Returns:
        Populated :class:`~nxp_monkey.PartDetails`.

    Raises:
        NxpFetchError: When the part cannot be fetched or its
            ``<PART>.xml`` is missing or malformed.
    """
    kex = client or KexClient()
    resolved_version = (
        version if version is not None else kex.resolve_version_for_family(part)
    )
    part_root = fetch(part, variant=variant, version=resolved_version, client=kex)

    header_path = part_root / f"{part}.xml"
    if not header_path.is_file():
        raise NxpFetchError(
            f"{part}: expected {header_path.name} at {part_root}, not found"
        )
    header = read_processor_header(header_path)

    cores = read_cores_info(part_root / "common" / "cores_info.xml")
    properties = read_processor_properties(part_root / "processor.properties")
    package_variants = find_package_variants(part_root)

    # Application-processor parts (i.MX 7/8/9 family) carry at least one
    # Cortex-A core. This signal is variant-stable: cores_info.xml lives
    # in common/ under every SDK variant. See xml_survey.md.
    is_application_processor = _has_application_core(cores)

    return PartDetails(
        part=part,
        variant_id=variant,
        version=resolved_version,
        header=header,
        cores=cores,
        variants=package_variants,
        properties=properties,
        root=part_root,
        is_application_processor=is_application_processor,
    )


def details_from_cache(
    part: str,
    *,
    variant: str = DEFAULT_VARIANT,
    version: str,
) -> PartDetails:
    """Build a :class:`PartDetails` from an already-populated cache dir.

    Offline counterpart to :func:`details`. Used by tests and by tools
    that have arranged the cache themselves. Raises if the cache root
    does not exist.

    Args:
        part: Processor id.
        variant: SDK variant directory name to load.
        version: Tool version directory name to load.

    Returns:
        Populated :class:`~nxp_monkey.PartDetails`.

    Raises:
        NxpFetchError: When the cache root or ``<PART>.xml`` are
            missing.
    """
    part_root = cache.kex_root() / version / "processors" / part / variant
    if not part_root.is_dir():
        raise NxpFetchError(
            f"{part}/{variant} not in cache at {part_root}; call fetch() first"
        )
    header_path = part_root / f"{part}.xml"
    if not header_path.is_file():
        raise NxpFetchError(
            f"{part}: expected {header_path.name} at {part_root}, not found"
        )
    cores = read_cores_info(part_root / "common" / "cores_info.xml")
    return PartDetails(
        part=part,
        variant_id=variant,
        version=version,
        header=read_processor_header(header_path),
        cores=cores,
        variants=find_package_variants(part_root),
        properties=read_processor_properties(part_root / "processor.properties"),
        root=part_root,
        is_application_processor=_has_application_core(cores),
    )


def _has_application_core(cores: tuple) -> bool:
    """Return True iff any core in ``cores`` is a Cortex-A class core.

    Used to flag i.MX 7/8/9 application processors. Variant-stable: it
    reads ``cores_info.xml``, which is identical between SDK variants.
    """
    return any(c.name.startswith("Cortex-A") for c in cores)
