"""Shared part-number matching helpers.

NXP's portfolio keys are often masked part names such as
``MIMX9352xxxxM``. User input may be a shorter family prefix
(``MIMX93``) or a longer concrete orderable number
(``MIMX9352CVVXMAB``). The helpers here keep that matching rule
consistent across search, fetch, and KEX canonicalization.
"""
from __future__ import annotations

from collections.abc import Iterable


def masked_part_match(pattern: str, value: str, *, allow_suffix: bool) -> bool:
    """Return True when ``value`` matches a masked NXP portfolio key.

    Literal characters in ``pattern`` must match exactly, case-insensitively.
    The letter ``x`` in ``pattern`` is treated as a wildcard for one
    character. When ``allow_suffix`` is true, extra characters in ``value``
    after the pattern are ignored; this handles orderable suffixes.
    """
    cleaned_pattern = pattern.strip().lower()
    cleaned_value = value.strip().lower()
    if not cleaned_pattern or len(cleaned_value) < len(cleaned_pattern):
        return False
    if not allow_suffix and len(cleaned_value) != len(cleaned_pattern):
        return False
    return all(
        pattern_char == "x" or pattern_char == value_char
        for pattern_char, value_char in zip(cleaned_pattern, cleaned_value, strict=False)
    )


def masked_part_prefix_match(pattern: str, value: str) -> bool:
    """Return True when ``value`` is a prefix of ``pattern`` with ``x`` wildcards."""
    cleaned_pattern = pattern.strip().lower()
    cleaned_value = value.strip().lower()
    if not cleaned_pattern or not cleaned_value:
        return False
    if len(cleaned_value) > len(cleaned_pattern):
        return False
    return all(
        pattern_char == "x" or pattern_char == value_char
        for pattern_char, value_char in zip(cleaned_pattern, cleaned_value, strict=False)
    )


def portfolio_part_matches(query: str, parts: Iterable[str]) -> list[str]:
    """Resolve ``query`` against portfolio keys using exact, alias, and prefix rules."""
    cleaned = query.strip()
    if not cleaned:
        return []

    keys = sorted(set(parts), key=str.lower)
    lowered = cleaned.lower()
    exact = [part for part in keys if part.lower() == lowered]
    if exact:
        return exact

    alias = [
        part
        for part in keys
        if "x" in part.lower() and masked_part_match(part, cleaned, allow_suffix=True)
    ]
    if alias:
        return alias

    prefix = [
        part
        for part in keys
        if part.lower().startswith(lowered) or masked_part_prefix_match(part, cleaned)
    ]
    return prefix
