"""Exercise :class:`nxp_monkey.KexClient` against offline fixtures."""
from __future__ import annotations

import ssl
from unittest.mock import MagicMock
from urllib import error

import certifi
import nxp_monkey
import pytest
from nxp_monkey import kex_client

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


@pytest.mark.parametrize("download", [False, True])
def test_kex_requests_use_fixed_roots_and_verify_hostnames(monkeypatch, tmp_path, download):
    """Metadata and archives use fixed roots even with stale host CA overrides."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "stale-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "stale-certs"))
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.side_effect = [b"payload", b""]
    response.headers = {"Content-Length": "7"}
    urlopen = MagicMock(return_value=response)
    monkeypatch.setattr(kex_client.request, "urlopen", urlopen)

    if download:
        client = nxp_monkey.KexClient(version="25.12.10")
        output = tmp_path / "archive.zip"
        assert client.download_directory_zip("MCXA156", "ksdk2_0", output) == 7
        assert output.read_bytes() == b"payload"
    else:
        assert kex_client._read_url_bytes(kex_client.NPIDB_BASE) == b"payload"

    context = urlopen.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    expected = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    expected.load_verify_locations(certifi.where())
    assert set(context.get_ca_certs(binary_form=True)) == set(
        expected.get_ca_certs(binary_form=True)
    )
    names = {
        value
        for certificate in context.get_ca_certs()
        for rdn in certificate["subject"]
        for key, value in rdn
        if key == "commonName"
    }
    assert names
    assert not names & {"emSign ECC Root CA - G3", "AAA Certificate Services"}
    assert tuple(map(int, certifi.__version__.split("."))) >= (2026, 10, 19)


@pytest.mark.parametrize("download", [False, True])
def test_kex_certificate_failure_never_disables_verification(monkeypatch, tmp_path, download):
    """Certificate errors fail closed through metadata and archive retry paths."""
    urlopen = MagicMock(
        side_effect=error.URLError(ssl.SSLCertVerificationError("untrusted issuer"))
    )
    monkeypatch.setattr(kex_client.request, "urlopen", urlopen)
    monkeypatch.setattr(kex_client.time, "sleep", lambda seconds: None)
    output = tmp_path / "archive.zip"
    with pytest.raises(nxp_monkey.NxpFetchError, match="untrusted issuer"):
        if download:
            client = nxp_monkey.KexClient(version="25.12.10", retries=0)
            client.download_directory_zip("MCXA156", "ksdk2_0", output)
        else:
            kex_client._read_url_bytes(kex_client.NPIDB_BASE)

    assert not output.exists()
    for call in urlopen.call_args_list:
        context = call.kwargs["context"]
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
