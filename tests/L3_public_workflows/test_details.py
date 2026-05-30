"""Exercise :func:`nxp_monkey.details_from_cache` against an offline fixture.

The live :func:`nxp_monkey.details` is covered indirectly: it shares
the spine-parsing path with ``details_from_cache``. Network hits are
opt-in via the ``network`` marker and not run by default.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import nxp_monkey
import pytest

FIXTURE_PART_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "parts"
    / "MCXSAMPLE"
    / "ksdk2_0"
)


def _stage_fixture(tmp_cache: Path) -> Path:
    """Copy the MCXSAMPLE fixture into a synthetic cache version dir."""
    version_root = tmp_cache / "kex" / "26.03" / "processors" / "MCXSAMPLE" / "ksdk2_0"
    shutil.copytree(FIXTURE_PART_ROOT, version_root)
    return version_root


def test_details_from_cache_returns_populated_structure(tmp_cache):
    """``details_from_cache`` parses header, cores, variants, properties."""
    _stage_fixture(tmp_cache)
    info = nxp_monkey.details_from_cache("MCXSAMPLE", variant="ksdk2_0", version="26.03")

    assert info.part == "MCXSAMPLE"
    assert info.variant_id == "ksdk2_0"
    assert info.version == "26.03"
    assert info.header.family == "MCX"
    assert info.header.series == "MCX SAMPLE"
    assert info.header.default_part == "MCXSAMPLE_QFN48"
    assert "Clocks" in info.header.enabled_tools
    assert info.properties == {"version": "26.03.99"}
    assert info.is_application_processor is False
    assert info.root.is_dir()


def test_details_from_cache_finds_both_variants(tmp_cache):
    """Both fixture variant subdirs are discovered and mapped to packages."""
    _stage_fixture(tmp_cache)
    info = nxp_monkey.details_from_cache("MCXSAMPLE", variant="ksdk2_0", version="26.03")

    variant_ids = {v.variant for v in info.variants}
    packages = {v.package for v in info.variants}
    assert variant_ids == {"MCXSAMPLE_QFN32", "MCXSAMPLE_QFN48"}
    assert packages == {"packages/QFN32.xml", "packages/QFN48.xml"}


def test_details_from_cache_link_by_type(tmp_cache):
    """``PackageVariant.link_by_type`` lifts a typed link out of the manifest."""
    _stage_fixture(tmp_cache)
    info = nxp_monkey.details_from_cache("MCXSAMPLE", variant="ksdk2_0", version="26.03")
    qfn48 = next(v for v in info.variants if v.variant == "MCXSAMPLE_QFN48")
    pkg_link = qfn48.link_by_type("package")
    assert pkg_link is not None
    assert pkg_link.link == "packages/QFN48.xml"
    assert qfn48.link_by_type("does-not-exist") is None


def test_details_from_cache_detects_application_processor(tmp_cache):
    """A Cortex-A core in ``cores_info.xml`` marks the part as app processor."""
    version_root = _stage_fixture(tmp_cache)
    cores_xml = version_root / "common" / "cores_info.xml"
    cores_xml.write_text(
        '<?xml version="1.0"?>'
        '<cores:cores xmlns:cores="http://swtools.freescale.net/XSD/processor/2.0/">'
        '  <core name="Cortex-A55" id="ca55_core0" description="A55 core"/>'
        '  <core name="Cortex-M33" id="cm33_core0" description="M33 coproc"/>'
        '</cores:cores>',
        encoding="utf-8",
    )
    info = nxp_monkey.details_from_cache("MCXSAMPLE", variant="ksdk2_0", version="26.03")
    assert info.is_application_processor is True


def test_details_from_cache_missing_cache_raises(tmp_cache):
    """Missing cache dir surfaces a clear NxpFetchError."""
    with pytest.raises(nxp_monkey.NxpFetchError):
        nxp_monkey.details_from_cache("MCXDOES_NOT_EXIST", variant="ksdk2_0", version="26.03")


def test_details_from_cache_missing_header_raises(tmp_cache):
    """Cache without the top-level ``<PART>.xml`` raises NxpFetchError."""
    version_root = _stage_fixture(tmp_cache)
    (version_root / "MCXSAMPLE.xml").unlink()
    with pytest.raises(nxp_monkey.NxpFetchError):
        nxp_monkey.details_from_cache("MCXSAMPLE", variant="ksdk2_0", version="26.03")
