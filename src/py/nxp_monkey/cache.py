"""Cross-platform cache directory management for ``nxp_monkey``.

The cache contract is documented in ADR-0006. The location is resolved via
``platformdirs`` so it works on Windows, macOS, and Linux without per-OS
branches in user code.

Public surface:

- :func:`cache_path` — return the cache root as a ``pathlib.Path``.
- :func:`cache_size` — return the total size on disk in bytes.
- :func:`cache_clear` — remove all (or a subset of) cached content.

Submodule paths used by other internal modules:

- ``cache_path() / "kex" / "versions.json"`` — cached version list.
- ``cache_path() / "kex" / "portfolio.json"`` — family-to-version map.
- ``cache_path() / "kex" / <version> / "processors" / ...`` — unpacked tree.
- ``cache_path() / "index" / "nxp_monkey_index.sqlite"`` — search index.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from platformdirs import user_cache_dir

_APP_NAME = "nxp_monkey"


def cache_path() -> Path:
    """Return the absolute path to the ``nxp_monkey`` cache root.

    The directory is created if it does not yet exist. See ADR-0006 for the
    full layout contract. ``platformdirs`` is called with ``appauthor=False``
    so no vendor parent directory is inserted on Windows — the cache lives
    directly under ``%LOCALAPPDATA%/nxp_monkey/`` (Windows),
    ``~/.cache/nxp_monkey/`` (Linux), or
    ``~/Library/Caches/nxp_monkey/`` (macOS).

    Returns:
        Absolute filesystem path of the cache root.
    """
    root = Path(user_cache_dir(_APP_NAME, appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    return root


def kex_root() -> Path:
    """Return the ``kex/`` subdirectory of the cache.

    Returns:
        Absolute filesystem path of the KEX cache subtree.
    """
    path = cache_path() / "kex"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_root() -> Path:
    """Return the ``index/`` subdirectory of the cache.

    Returns:
        Absolute filesystem path of the index subtree.
    """
    path = cache_path() / "index"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_size() -> int:
    """Return the total byte size of all files under the cache root.

    Returns:
        Total size in bytes. Returns ``0`` if the cache root does not exist.
    """
    root = cache_path()
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def cache_clear(scope: str = "all") -> None:
    """Remove cached content.

    Args:
        scope: One of:

            - ``"all"`` — remove every file under the cache root.
            - ``"versions"`` — remove only ``kex/versions.json`` and
              ``kex/portfolio.json``.
            - ``"index"`` — remove the ``index/`` subtree only.
            - any other string — treated as a KEX tool version
              (for example ``"25.12.10"``); removes
              ``kex/<version>/`` only.
    """
    root = cache_path()
    if scope == "all":
        for entry in root.iterdir():
            _remove(entry)
        return

    if scope == "versions":
        for name in ("versions.json", "portfolio.json"):
            target = kex_root() / name
            if target.exists():
                target.unlink()
        return

    if scope == "index":
        _remove(index_root())
        index_root()
        return

    # Treat as a tool version label.
    target = kex_root() / scope
    if target.exists():
        _remove(target)


def _remove(path: Path) -> None:
    """Delete a file or directory tree if it exists."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        try:
            path.unlink()
        except OSError:
            pass
