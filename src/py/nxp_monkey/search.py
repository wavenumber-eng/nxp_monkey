"""Search over the local index, with partial and fuzzy matching.

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
from difflib import SequenceMatcher

from .index import open_index
from .models import SearchHit

DEFAULT_LIMIT = 50
FUZZY_THRESHOLD = 0.55


def search(
    query: str,
    *,
    fuzzy: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Search the local index for parts matching ``query``.

    Args:
        query: Free-form search string. Case-insensitive.
        fuzzy: When True, also match on Levenshtein-style ratio similarity
            in addition to exact and substring matches.
        limit: Maximum number of hits to return.

    Returns:
        Ranked list of :class:`SearchHit`. May be empty if the index has
        not been built or there are no matches above threshold.
    """
    cleaned = query.strip()
    if not cleaned:
        return []

    try:
        conn = open_index(read_only=True)
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT part, family, variants FROM parts"
        ).fetchall()
    finally:
        conn.close()

    hits: list[SearchHit] = []
    lowered = cleaned.lower()
    for part, family, _variants_json in rows:
        part_l = part.lower()
        family_l = family.lower()

        if part_l == lowered:
            hits.append(SearchHit(part=part, family=family, score=1.0, matched_field="part"))
            continue
        if family_l == lowered:
            hits.append(SearchHit(part=part, family=family, score=0.95, matched_field="family"))
            continue
        if part_l.startswith(lowered):
            hits.append(SearchHit(part=part, family=family, score=0.9, matched_field="part"))
            continue
        if family_l.startswith(lowered):
            hits.append(SearchHit(part=part, family=family, score=0.85, matched_field="family"))
            continue
        if lowered in part_l:
            hits.append(SearchHit(part=part, family=family, score=0.7, matched_field="part"))
            continue
        if lowered in family_l:
            hits.append(SearchHit(part=part, family=family, score=0.65, matched_field="family"))
            continue
        if fuzzy:
            ratio_part = SequenceMatcher(None, lowered, part_l).ratio()
            ratio_family = SequenceMatcher(None, lowered, family_l).ratio()
            best = max(ratio_part, ratio_family)
            if best >= FUZZY_THRESHOLD:
                matched = "part" if ratio_part >= ratio_family else "family"
                # Scale fuzzy scores into [0.0, 0.6] so they always rank
                # below substring matches.
                scaled = 0.6 * best
                hits.append(
                    SearchHit(
                        part=part, family=family, score=scaled, matched_field=matched
                    )
                )

    hits.sort(key=lambda h: (-h.score, h.part.lower()))
    return hits[:limit]


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
