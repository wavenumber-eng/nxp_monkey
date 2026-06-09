"""Search indexed or portfolio parts, with partial and fuzzy matching.

Public surface:

- :func:`search` — return a ranked list of :class:`SearchHit`.

Three matching modes are supported and combined:

1. exact case-insensitive (top score);
2. prefix / substring (high score, partial match per the v0.1 goal);
3. fuzzy via :func:`difflib.SequenceMatcher` ratio (only when ``fuzzy=True``).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from difflib import SequenceMatcher

from ._matching import masked_part_match, masked_part_prefix_match
from .index import open_index
from .kex_client import KexClient, NxpFetchError
from .models import SearchHit

DEFAULT_LIMIT = 50
FUZZY_THRESHOLD = 0.55


def search(
    query: str,
    *,
    fuzzy: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Search indexed or portfolio parts matching ``query``.

    Args:
        query: Free-form search string. Case-insensitive.
        fuzzy: When True, also match on Levenshtein-style ratio similarity
            in addition to exact and substring matches.
        limit: Maximum number of hits to return.

    Returns:
        Ranked list of :class:`SearchHit`. Falls back to the cached/live
        portfolio when the SQLite index has not been built.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    rows = _indexed_rows()
    if rows is None:
        rows = _portfolio_rows()
    return _rank_rows(cleaned, rows, fuzzy=fuzzy, limit=limit)


def _indexed_rows() -> list[tuple[str, str]] | None:
    """Return indexed ``(part, family)`` rows, or None when no index exists."""
    try:
        conn = open_index(read_only=True)
    except sqlite3.OperationalError:
        return None

    try:
        rows = conn.execute("SELECT part, family FROM parts").fetchall()
    finally:
        conn.close()
    return [(part, family) for part, family in rows]


def _portfolio_rows() -> list[tuple[str, str]]:
    """Return portfolio rows for search fallback when the local index is absent."""
    try:
        portfolio = KexClient().portfolio_latest_map()
    except NxpFetchError:
        return []
    return [(part, part) for part in portfolio]


def _rank_rows(
    cleaned: str,
    rows: Iterable[tuple[str, str]],
    *,
    fuzzy: bool,
    limit: int,
) -> list[SearchHit]:
    """Rank raw ``(part, family)`` rows for ``cleaned`` query."""
    hits: list[SearchHit] = []
    for part, family in rows:
        hit = _score_row(cleaned, part, family, fuzzy=fuzzy)
        if hit is not None:
            hits.append(hit)
    hits.sort(key=lambda h: (-h.score, h.part.lower()))
    return hits[:limit]


def _score_row(
    cleaned: str,
    part: str,
    family: str,
    *,
    fuzzy: bool,
) -> SearchHit | None:
    """Score one row for ``cleaned`` query."""
    lowered = cleaned.lower()
    part_l = part.lower()
    family_l = family.lower()

    checks = (
        (part_l == lowered, 1.0, "part"),
        (_is_orderable_alias(part, cleaned), 0.98, "part"),
        (family_l == lowered, 0.95, "family"),
        (_is_part_prefix(part, cleaned), 0.90, "part"),
        (family_l.startswith(lowered), 0.85, "family"),
        (lowered in part_l, 0.70, "part"),
        (lowered in family_l, 0.65, "family"),
    )
    for matched, score, field in checks:
        if matched:
            return SearchHit(part=part, family=family, score=score, matched_field=field)
    if fuzzy:
        return _fuzzy_hit(lowered, part, family)
    return None


def _is_orderable_alias(part: str, cleaned: str) -> bool:
    """Return True when ``cleaned`` is a full orderable alias for ``part``."""
    return "x" in part.lower() and masked_part_match(part, cleaned, allow_suffix=True)


def _is_part_prefix(part: str, cleaned: str) -> bool:
    """Return True when ``cleaned`` is a literal or masked prefix of ``part``."""
    return part.lower().startswith(cleaned.lower()) or masked_part_prefix_match(part, cleaned)


def _fuzzy_hit(lowered: str, part: str, family: str) -> SearchHit | None:
    """Return a fuzzy match hit for one row, or None below threshold."""
    ratio_part = SequenceMatcher(None, lowered, part.lower()).ratio()
    ratio_family = SequenceMatcher(None, lowered, family.lower()).ratio()
    best = max(ratio_part, ratio_family)
    if best < FUZZY_THRESHOLD:
        return None
    matched = "part" if ratio_part >= ratio_family else "family"
    # Scale fuzzy scores into [0.0, 0.6] so they always rank below substring matches.
    return SearchHit(part=part, family=family, score=0.6 * best, matched_field=matched)


def part_variants(part: str) -> list[str]:
    """Return the recorded variant list for ``part`` from the index.

    Args:
        part: Part identifier (case-insensitive lookup).

    Returns:
        Sorted list of variant names. Empty list if the part is unknown.
    """
    try:
        conn = open_index(read_only=True)
    except sqlite3.OperationalError:
        return []
    try:
        row = conn.execute(
            "SELECT variants FROM parts WHERE LOWER(part) = LOWER(?)",
            (part,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return []
    return list(json.loads(row[0]))
