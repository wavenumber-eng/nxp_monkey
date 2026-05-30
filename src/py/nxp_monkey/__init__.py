"""Public Python surface for ``nxp_monkey``.

Importing ``nxp_monkey`` exposes the stable library API documented under
``docs/design/api/``. The CLI in :mod:`nxp_monkey._cli` is a thin wrapper
around these symbols; see ADR-0002 for the library-first design rule.
"""
from __future__ import annotations

from ._version import __version__
from .cache import cache_clear, cache_path, cache_size
from .details import details, details_from_cache
from .fetch import DEFAULT_SDK_VARIANTS, DEFAULT_VARIANT, fetch, fetch_all
from .index import build_index, get_part, index_meta, open_index
from .kex_client import (
    KexClient,
    NxpFetchError,
    latest_version,
    list_families,
    list_versions,
    portfolio_latest_map,
)
from .models import (
    ApiVersion,
    CpuCore,
    DbLink,
    IndexMeta,
    PackageVariant,
    PartDetails,
    PartInfo,
    Processor,
    SearchHit,
    StorageEntry,
)
from .roadmap import build_roadmap
from .search import part_variants, search
from .xml_json import mirror_xml_tree_as_json, xml_file_to_dict

__all__ = [
    "__version__",
    "ApiVersion",
    "CpuCore",
    "DbLink",
    "DEFAULT_SDK_VARIANTS",
    "DEFAULT_VARIANT",
    "IndexMeta",
    "KexClient",
    "NxpFetchError",
    "PackageVariant",
    "PartDetails",
    "PartInfo",
    "Processor",
    "SearchHit",
    "StorageEntry",
    "build_index",
    "build_roadmap",
    "cache_clear",
    "cache_path",
    "cache_size",
    "details",
    "details_from_cache",
    "fetch",
    "fetch_all",
    "get_part",
    "index_meta",
    "latest_version",
    "list_families",
    "list_versions",
    "mirror_xml_tree_as_json",
    "open_index",
    "part_variants",
    "portfolio_latest_map",
    "search",
    "xml_file_to_dict",
]
