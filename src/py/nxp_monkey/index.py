"""SQLite + FTS5 index over NXP parts and families.

Public surface:

- :func:`build_index` — populate the index from the KEX upstream.
- :func:`open_index` — open a read-only connection to the index database.
- :func:`get_part` — return a :class:`PartInfo` for a single part.
- :func:`index_meta` — return :class:`IndexMeta` for the latest build.

The index lives at ``cache.index_root() / "nxp_monkey_index.sqlite"``
(ADR-0006). Schema:

```
parts(part TEXT PRIMARY KEY, family TEXT, version TEXT, variants TEXT)
families(family TEXT PRIMARY KEY, version TEXT)
parts_fts USING fts5(part, family, content='parts')
meta(key TEXT PRIMARY KEY, value TEXT)
```
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from . import cache
from .fetch import DEFAULT_SDK_VARIANTS
from .kex_client import KexClient
from .models import IndexMeta, PartInfo

INDEX_FILENAME = "nxp_monkey_index.sqlite"
META_FILENAME = "nxp_monkey_index.meta.json"


def _index_db_path() -> str:
    """Return the on-disk path of the index database as a string."""
    return str(cache.index_root() / INDEX_FILENAME)


def open_index(*, read_only: bool = True) -> sqlite3.Connection:
    """Open a connection to the index database.

    Args:
        read_only: When True (the default), open in read-only mode using
            the SQLite URI scheme.

    Returns:
        A live :class:`sqlite3.Connection`. Caller is responsible for
        closing.

    Raises:
        sqlite3.OperationalError: If the database does not exist and
            ``read_only`` is True.
    """
    if read_only:
        return sqlite3.connect(
            f"file:{_index_db_path()}?mode=ro", uri=True
        )
    conn = sqlite3.connect(_index_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create index tables if they do not yet exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS parts (
            part    TEXT PRIMARY KEY,
            family  TEXT NOT NULL,
            version TEXT NOT NULL,
            variants TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS families (
            family  TEXT PRIMARY KEY,
            version TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS parts_fts USING fts5(
            part, family, content='parts', content_rowid='rowid'
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def build_index(
    *,
    variant: str | None = None,
    probe_variants: bool = False,
    client: KexClient | None = None,
) -> IndexMeta:
    """Build or refresh the local search index.

    The index is built from the KEX portfolio-latest view (every family
    mapped to its newest publishing version).

    Args:
        variant: When provided, restrict recorded variants to a single
            name. ``None`` records the full default variant set.
        probe_variants: When True, hit upstream to discover which variants
            are actually present for each family. This is much slower
            because it issues one extra request per family. When False
            (the default), the index records the assumed variant set
            ``DEFAULT_SDK_VARIANTS`` without probing.
        client: Optional :class:`KexClient` to reuse.

    Returns:
        :class:`IndexMeta` describing the build.
    """
    kex = client or KexClient()
    portfolio = kex.portfolio_latest_map()

    conn = sqlite3.connect(_index_db_path())
    try:
        _ensure_schema(conn)
        with conn:
            conn.execute("DELETE FROM parts")
            conn.execute("DELETE FROM families")
            conn.execute("DELETE FROM parts_fts")

            for family, family_version in portfolio.items():
                if probe_variants:
                    variants = kex.discover_family_variants(
                        family, version=family_version
                    )
                elif variant is not None:
                    variants = [variant]
                else:
                    variants = list(DEFAULT_SDK_VARIANTS)
                conn.execute(
                    "INSERT INTO families(family, version) VALUES (?, ?)",
                    (family, family_version),
                )
                conn.execute(
                    "INSERT INTO parts(part, family, version, variants) "
                    "VALUES (?, ?, ?, ?)",
                    (family, family, family_version, json.dumps(sorted(variants))),
                )
                conn.execute(
                    "INSERT INTO parts_fts(rowid, part, family) "
                    "SELECT rowid, part, family FROM parts WHERE part = ?",
                    (family,),
                )

            meta = IndexMeta(
                built_at=datetime.now(UTC).isoformat(),
                source_version="portfolio-latest",
                part_count=conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0],
                family_count=conn.execute(
                    "SELECT COUNT(*) FROM families"
                ).fetchone()[0],
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("built_at", meta.built_at),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                ("source_version", meta.source_version),
            )

        # Sidecar JSON for human / agent inspection.
        sidecar = cache.index_root() / META_FILENAME
        sidecar.write_text(
            json.dumps(
                {
                    "built_at": meta.built_at,
                    "source_version": meta.source_version,
                    "part_count": meta.part_count,
                    "family_count": meta.family_count,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return meta
    finally:
        conn.close()


def get_part(part: str) -> PartInfo | None:
    """Look up a single part in the local index.

    Args:
        part: Part / family identifier (case-insensitive).

    Returns:
        :class:`PartInfo` if the part is present in the index, else
        ``None``.
    """
    try:
        conn = open_index(read_only=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT part, family, version, variants FROM parts "
            "WHERE LOWER(part) = LOWER(?)",
            (part,),
        ).fetchone()
        if row is None:
            return None
        part_name, family, version, variants_json = row
        return PartInfo(
            part=part_name,
            family=family,
            version=version,
            variants=tuple(json.loads(variants_json)),
        )
    finally:
        conn.close()


def index_meta() -> IndexMeta | None:
    """Return metadata for the current index, or ``None`` if absent.

    Returns:
        :class:`IndexMeta` if the index has been built, else ``None``.
    """
    sidecar = cache.index_root() / META_FILENAME
    if not sidecar.exists():
        return None
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    return IndexMeta(
        built_at=payload["built_at"],
        source_version=payload["source_version"],
        part_count=payload["part_count"],
        family_count=payload["family_count"],
    )
