"""Exercise the public :func:`nxp_monkey.fetch` and helpers."""
from __future__ import annotations

import nxp_monkey
import pytest
from nxp_monkey.fetch import extract_processor_zip


def test_extract_processor_zip_handles_processors_layout(tmp_path, fixture_zip):
    """A well-formed KEX-style zip extracts under ``processors/``."""
    out = tmp_path / "out"
    stats = extract_processor_zip(fixture_zip, out)
    assert stats["files"] == 2
    assert (out / "processors" / "SAMPLE" / "ksdk2_0" / "info.txt").is_file()


def test_extract_processor_zip_rejects_path_traversal(tmp_path):
    """Members containing ``..`` are rejected."""
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("processors/../../../escape.txt", "nope")

    with pytest.raises(nxp_monkey.NxpFetchError):
        extract_processor_zip(bad, tmp_path / "out")


def test_extract_processor_zip_normalizes_kex_tools_prefix(tmp_path):
    """Members that start with ``kex_tools/`` are rewritten under ``processors/``."""
    import zipfile

    src = tmp_path / "src.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("kex_tools/processors/SAMPLE/ksdk2_0/x.txt", "ok")

    out = tmp_path / "out"
    extract_processor_zip(src, out)
    assert (out / "processors" / "SAMPLE" / "ksdk2_0" / "x.txt").is_file()


def test_fetch_is_idempotent_when_target_exists(tmp_cache, monkeypatch, fixture_zip):
    """A second :func:`fetch` call with the target populated is a no-op."""
    from nxp_monkey.kex_client import KexClient

    target = (
        tmp_cache / "kex" / "25.12.10" / "processors" / "SAMPLE" / "ksdk2_0"
    )
    target.mkdir(parents=True)
    (target / "marker.txt").write_text("already here")

    called = {"count": 0}

    def fake_download(self, family, variant, output_zip, *, version=None):  # noqa: ARG001
        called["count"] += 1
        return 0

    monkeypatch.setattr(
        KexClient, "download_directory_zip", fake_download
    )
    monkeypatch.setattr(
        KexClient,
        "canonicalize_family",
        lambda self, family: family,
    )
    monkeypatch.setattr(
        KexClient,
        "resolve_version_for_family",
        lambda self, family: "25.12.10",
    )

    result = nxp_monkey.fetch("SAMPLE")
    assert result == target
    assert called["count"] == 0
