"""Exercise :class:`nxp_monkey.KexClient` against offline fixtures."""
from __future__ import annotations

import nxp_monkey
import pytest

from tests.support_scripts.fake_urlopen import patch_urlopen


def _responder_factory(versions_xml: bytes, processors_xml: bytes, family_xml: bytes):
    def respond(url: str) -> bytes:
        if "npidb/versions" in url:
            return versions_xml
        if url.endswith("processors?cmd=dir") or "processors?cmd=dir" in url:
            return processors_xml
        if "/MCXA156" in url and "cmd=dir" in url:
            return family_xml
        return b"<dir/>"

    return respond


def test_list_versions_parses_fixture(
    monkeypatch, fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
):
    """``KexClient.list_versions`` parses captured XML into ``ApiVersion`` rows."""
    with patch_urlopen(
        monkeypatch,
        _responder_factory(
            fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
        ),
    ):
        client = nxp_monkey.KexClient()
        versions = client.list_versions()
    names = [v.name for v in versions]
    assert "25.12.10" in names
    assert "26.3.0" in names


def test_latest_version_skips_unpublished(
    monkeypatch, fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
):
    """The unpublished ``26.3.0`` row is skipped by default."""
    with patch_urlopen(
        monkeypatch,
        _responder_factory(
            fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
        ),
    ):
        assert nxp_monkey.latest_version() == "25.12.10"


def test_list_families_returns_sorted(
    monkeypatch, fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
):
    """``list_families`` returns directory entries in case-insensitive name order."""
    with patch_urlopen(
        monkeypatch,
        _responder_factory(
            fixture_versions_xml, fixture_processors_dir_xml, fixture_family_dir_xml
        ),
    ):
        families = nxp_monkey.list_families()
    names = [f.name for f in families]
    assert names == sorted(names, key=str.lower)
    assert "MCXA156" in names


def test_nxp_fetch_error_on_bad_xml(monkeypatch):
    """Invalid XML upstream raises :class:`NxpFetchError`."""

    def bad_xml(_url: str) -> bytes:
        return b"<not really xml"

    with patch_urlopen(monkeypatch, bad_xml):
        client = nxp_monkey.KexClient()
        with pytest.raises(nxp_monkey.NxpFetchError):
            client.list_versions()


def test_canonicalize_family_accepts_orderable_mask_alias(monkeypatch):
    """Concrete orderable MPNs can resolve to one masked portfolio key."""

    def fake_portfolio_map(self, *, refresh=False):  # noqa: ARG001
        return {
            "MIMX9352xxxxM": "26.03",
            "MIMX9301xxxxD": "26.03",
        }

    monkeypatch.setattr(nxp_monkey.KexClient, "portfolio_latest_map", fake_portfolio_map)
    client = nxp_monkey.KexClient()
    assert client.canonicalize_family("MIMX9352CVVXMAB") == "MIMX9352xxxxM"


def test_canonicalize_family_reports_ambiguous_prefix(monkeypatch):
    """A prefix that matches multiple portfolio keys raises a useful error."""

    def fake_portfolio_map(self, *, refresh=False):  # noqa: ARG001
        return {
            "MIMX9352xxxxM": "26.03",
            "MIMX9301xxxxD": "26.03",
        }

    monkeypatch.setattr(nxp_monkey.KexClient, "portfolio_latest_map", fake_portfolio_map)
    client = nxp_monkey.KexClient()
    with pytest.raises(nxp_monkey.NxpFetchError, match="matches 2 processor families"):
        client.canonicalize_family("MIMX93")
