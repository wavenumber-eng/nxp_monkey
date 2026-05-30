"""Exercise the public ``nxp_monkey.cache`` surface."""
from __future__ import annotations

import nxp_monkey


def test_cache_path_returns_dir(tmp_cache):
    """``cache_path`` returns the monkeypatched temporary directory."""
    assert nxp_monkey.cache_path() == tmp_cache


def test_cache_size_zero_for_empty_dir(tmp_cache):
    """An empty cache reports size 0."""
    assert nxp_monkey.cache_size() == 0


def test_cache_size_sums_file_bytes(tmp_cache):
    """File bytes are summed across the cache tree."""
    (tmp_cache / "kex").mkdir(exist_ok=True)
    (tmp_cache / "kex" / "a.txt").write_bytes(b"hello")
    (tmp_cache / "kex" / "b.txt").write_bytes(b"world!")
    assert nxp_monkey.cache_size() == 5 + 6


def test_cache_clear_all_removes_content(tmp_cache):
    """``cache_clear('all')`` removes every entry under the cache root."""
    (tmp_cache / "kex").mkdir(exist_ok=True)
    (tmp_cache / "kex" / "a.txt").write_bytes(b"data")
    nxp_monkey.cache_clear("all")
    assert list(tmp_cache.iterdir()) == []


def test_cache_clear_versions_only(tmp_cache):
    """``cache_clear('versions')`` removes only the cached version JSONs."""
    from nxp_monkey.cache import kex_root

    root = kex_root()
    (root / "versions.json").write_text("[]")
    (root / "portfolio.json").write_text("{}")
    (root / "25.12.10").mkdir()

    nxp_monkey.cache_clear("versions")
    assert not (root / "versions.json").exists()
    assert not (root / "portfolio.json").exists()
    assert (root / "25.12.10").exists()
