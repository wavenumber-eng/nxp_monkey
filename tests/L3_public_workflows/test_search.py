"""Exercise :func:`nxp_monkey.search`."""
from __future__ import annotations

import nxp_monkey


def test_search_exact_match_top(populated_index):
    """An exact part name ranks first with score 1.0."""
    hits = nxp_monkey.search("MCXA156")
    assert hits
    assert hits[0].part == "MCXA156"
    assert hits[0].score == 1.0


def test_search_prefix_matches(populated_index):
    """A prefix query returns every part with that prefix."""
    hits = nxp_monkey.search("MCXA")
    parts = {h.part for h in hits}
    assert {"MCXA156", "MCXA266"}.issubset(parts)
    assert "JN5188" not in parts


def test_search_case_insensitive(populated_index):
    """Search is case-insensitive."""
    hits_lower = nxp_monkey.search("mcxa156")
    hits_upper = nxp_monkey.search("MCXA156")
    assert hits_lower and hits_upper
    assert hits_lower[0].part == hits_upper[0].part


def test_search_fuzzy_matches_typo(populated_index):
    """``--fuzzy`` recovers a typo'd query."""
    hits = nxp_monkey.search("mcxa157", fuzzy=True)
    parts = [h.part for h in hits]
    # MCXA156 is one edit away; should appear.
    assert "MCXA156" in parts


def test_search_returns_empty_for_unknown(populated_index):
    """No hits and no error for a fully unknown query."""
    hits = nxp_monkey.search("ZZZ_NOT_A_PART")
    assert hits == []


def test_search_falls_back_to_portfolio_without_index(monkeypatch):
    """Search still finds prefix matches when the SQLite index is absent."""
    from nxp_monkey.kex_client import KexClient

    def fake_portfolio_map(self, *, refresh=False):  # noqa: ARG001
        return {
            "MIMX9301xxxxD": "26.03",
            "MIMX9352xxxxM": "26.03",
            "MCXA156": "26.03",
        }

    monkeypatch.setattr(KexClient, "portfolio_latest_map", fake_portfolio_map)
    hits = nxp_monkey.search("MIMX93")
    parts = {hit.part for hit in hits}
    assert {"MIMX9301xxxxD", "MIMX9352xxxxM"}.issubset(parts)
    assert "MCXA156" not in parts


def test_search_matches_orderable_mpn_to_masked_portfolio_key(monkeypatch):
    """A concrete orderable part number resolves to the masked NXP key."""
    from nxp_monkey.kex_client import KexClient

    def fake_portfolio_map(self, *, refresh=False):  # noqa: ARG001
        return {
            "MIMX9352xxxxM": "26.03",
            "MIMX9301xxxxD": "26.03",
        }

    monkeypatch.setattr(KexClient, "portfolio_latest_map", fake_portfolio_map)
    hits = nxp_monkey.search("MIMX9352CVVXMAB")
    assert hits
    assert hits[0].part == "MIMX9352xxxxM"
    assert hits[0].score == 0.98


def test_part_variants_reports_known(populated_index):
    """``part_variants`` returns the variant list recorded by ``build_index``."""
    variants = nxp_monkey.part_variants("MCXA156")
    assert "ksdk2_0" in variants
