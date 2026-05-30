"""Shared pytest fixtures for nxp_monkey tests."""
from __future__ import annotations

import functools
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _install_fake_cache_path(monkeypatch, target_dir: Path) -> None:
    """Redirect cache_path() to ``target_dir`` everywhere it is imported.

    The package re-exports ``cache_path`` from ``nxp_monkey/__init__.py``,
    so the symbol must be patched in both the source module and the
    top-level namespace. ``functools.wraps`` preserves the docstring so
    L99 docstring checks still see the original PEP 257 doc.
    """
    import nxp_monkey
    from nxp_monkey import cache as cache_module

    original = cache_module.cache_path

    @functools.wraps(original)
    def _fake_cache_path() -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    monkeypatch.setattr(cache_module, "cache_path", _fake_cache_path)
    monkeypatch.setattr(nxp_monkey, "cache_path", _fake_cache_path)


@pytest.fixture()
def tmp_cache(tmp_path, monkeypatch):
    """Redirect the nxp_monkey cache to a per-test temporary directory.

    Yields the path of the temporary cache root.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    _install_fake_cache_path(monkeypatch, tmp_path)
    yield tmp_path


@pytest.fixture()
def fixture_versions_xml() -> bytes:
    """Return the captured KEX versions.xml payload as bytes."""
    return (FIXTURES / "kex" / "versions.xml").read_bytes()


@pytest.fixture()
def fixture_processors_dir_xml() -> bytes:
    """Return a captured KEX processors directory listing as bytes."""
    return (FIXTURES / "kex" / "processors_dir.xml").read_bytes()


@pytest.fixture()
def fixture_family_dir_xml() -> bytes:
    """Return a captured single-family directory listing as bytes."""
    return (FIXTURES / "kex" / "family_dir.xml").read_bytes()


@pytest.fixture()
def fixture_zip(tmp_path) -> Path:
    """Return the path to a tiny on-disk ZIP that mimics a KEX archive."""
    import zipfile

    target = tmp_path / "fixture.zip"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("processors/SAMPLE/ksdk2_0/info.txt", "hello\n")
        zf.writestr(
            "processors/SAMPLE/ksdk2_0/signal_configuration.xml",
            "<root/>",
        )
    return target


@pytest.fixture()
def populated_index(tmp_cache, monkeypatch):
    """Pre-populate a small index in the temporary cache.

    Yields the :class:`IndexMeta` describing the built index.
    """
    from nxp_monkey import index as index_module
    from nxp_monkey.kex_client import KexClient

    fake_portfolio = {
        "MCXA156": "25.12.10",
        "MCXA266": "25.12.10",
        "MCXN947": "25.12.10",
        "JN5188": "24.12.0",
    }

    def fake_portfolio_map(self, *, refresh=False):  # noqa: ARG001
        return dict(fake_portfolio)

    monkeypatch.setattr(KexClient, "portfolio_latest_map", fake_portfolio_map)
    meta = index_module.build_index(probe_variants=False)
    yield meta


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch, request):
    """Always isolate the cache for tests unless they opt out.

    L0 CLI subprocess tests run in their own process so they need their own
    cache dir provisioned via an environment variable; for unit tests we
    monkeypatch the in-process resolver.
    """
    if "no_isolate_cache" in request.keywords:
        return
    cache_dir = tmp_path / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _install_fake_cache_path(monkeypatch, cache_dir)
