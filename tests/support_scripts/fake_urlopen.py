"""Minimal urlopen fake for offline KEX tests."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from io import BytesIO

Responder = Callable[[str], bytes]


class _FakeResponse:
    """Tiny stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, payload: bytes) -> None:
        self._buf = BytesIO(payload)

    def read(self, n: int | None = None) -> bytes:
        """Read up to ``n`` bytes from the underlying buffer."""
        if n is None:
            return self._buf.read()
        return self._buf.read(n)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@contextmanager
def patch_urlopen(monkeypatch, responder: Responder):
    """Patch :func:`urllib.request.urlopen` used by ``kex_client`` and ``fetch``.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        responder: Function that maps a URL to a bytes payload.

    Yields:
        None.
    """
    from nxp_monkey import fetch as fetch_mod
    from nxp_monkey import kex_client as kex_mod

    def fake(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        return _FakeResponse(responder(url))

    monkeypatch.setattr(kex_mod.request, "urlopen", fake)
    monkeypatch.setattr(fetch_mod, "request", kex_mod.request, raising=False)
    yield
