"""Exercise the public dataclasses in :mod:`nxp_monkey.models`."""
from __future__ import annotations

import nxp_monkey


def test_api_version_stable_property():
    """``ApiVersion.stable`` is True iff ``version`` is non-empty."""
    stable = nxp_monkey.ApiVersion(api_id=1, name="25.12.10", version="25.12.10")
    unstable = nxp_monkey.ApiVersion(api_id=2, name="26.3.0", version="")
    assert stable.stable is True
    assert unstable.stable is False


def test_storage_entry_name_property():
    """``StorageEntry.name`` returns the basename of ``full_path``."""
    entry = nxp_monkey.StorageEntry(
        file_id="x",
        full_path="/kex_tools/processors/MCXA156",
        directory=True,
        time=None,
        size=None,
        compression="",
    )
    assert entry.name == "MCXA156"


def test_part_info_fields():
    """``PartInfo`` round-trips through the dataclass interface."""
    info = nxp_monkey.PartInfo(
        part="MCXA156",
        family="MCXA156",
        version="25.12.10",
        variants=("ksdk2_0", "zephyr3_2"),
    )
    assert info.variants == ("ksdk2_0", "zephyr3_2")
    assert info.part == "MCXA156"


def test_search_hit_ordering_is_stable_by_dataclass_eq():
    """Two equal SearchHit instances are equal under dataclass eq."""
    a = nxp_monkey.SearchHit(part="X", family="X", score=1.0, matched_field="part")
    b = nxp_monkey.SearchHit(part="X", family="X", score=1.0, matched_field="part")
    assert a == b


def test_index_meta_fields():
    """``IndexMeta`` exposes the documented fields."""
    meta = nxp_monkey.IndexMeta(
        built_at="2026-05-29T00:00:00+00:00",
        source_version="portfolio-latest",
        part_count=3,
        family_count=3,
    )
    assert meta.part_count == 3
    assert meta.source_version == "portfolio-latest"


def test_processor_fields():
    """``Processor`` round-trips through the dataclass interface."""
    proc = nxp_monkey.Processor(
        part="MCXA132",
        producer="NXP",
        family="MCX",
        series="MCX MCXA",
        default_part="MCXA132VLF",
        target_products=("MCUX",),
        enabled_tools=("Pins", "Clocks", "Peripherals"),
    )
    assert proc.family == "MCX"
    assert proc.target_products == ("MCUX",)
    assert "Clocks" in proc.enabled_tools


def test_db_link_fields():
    """``DbLink`` carries the verbatim manifest row."""
    link = nxp_monkey.DbLink(
        type="package",
        link="packages/QFN32.xml",
        format_version="1.0",
        description="QFN32 package definition",
    )
    assert link.type == "package"
    assert link.link.endswith(".xml")


def test_cpu_core_fields():
    """``CpuCore`` exposes the documented fields."""
    core = nxp_monkey.CpuCore(name="Cortex-M33", core_id="cm33_core0", description="M33 core")
    assert core.name == "Cortex-M33"
    assert core.core_id == "cm33_core0"


def test_package_variant_link_by_type():
    """``PackageVariant.link_by_type`` returns the first match or None."""
    from pathlib import Path

    pkg = nxp_monkey.DbLink(
        type="package",
        link="packages/QFN32.xml",
        format_version="1.0",
        description="",
    )
    regs = nxp_monkey.DbLink(
        type="registers",
        link="V/registers/registers.xml",
        format_version="6.0",
        description="",
    )
    variant = nxp_monkey.PackageVariant(
        variant="MCXA132VFM",
        package="packages/QFN32.xml",
        db_links=(pkg, regs),
        root=Path("."),
    )
    assert variant.link_by_type("registers") is regs
    assert variant.link_by_type("missing") is None


def test_part_details_default_fields():
    """``PartDetails`` exposes header, cores, variants, and the cache root."""
    from pathlib import Path

    header = nxp_monkey.Processor(
        part="X",
        producer="NXP",
        family="MCX",
        series="",
        default_part="",
        target_products=(),
        enabled_tools=(),
    )
    info = nxp_monkey.PartDetails(
        part="X",
        variant_id="ksdk2_0",
        version="26.03",
        header=header,
        cores=(),
        variants=(),
        properties={"version": "26.03"},
        root=Path("."),
        is_application_processor=False,
    )
    assert info.header.family == "MCX"
    assert info.properties == {"version": "26.03"}
    assert info.is_application_processor is False
