"""Exercise the local SQLite index."""
from __future__ import annotations

import nxp_monkey


def test_build_index_populates_meta(populated_index):
    """``build_index`` records meta with non-zero counts."""
    meta = populated_index
    assert meta.part_count >= 1
    assert meta.family_count >= 1
    assert meta.source_version == "portfolio-latest"


def test_get_part_returns_part_info(populated_index):
    """``get_part`` returns a ``PartInfo`` for a known part."""
    info = nxp_monkey.get_part("MCXA156")
    assert info is not None
    assert info.part == "MCXA156"
    assert info.version == "25.12.10"
    assert "ksdk2_0" in info.variants


def test_get_part_returns_none_for_unknown(populated_index):
    """``get_part`` returns ``None`` for an unknown part."""
    assert nxp_monkey.get_part("NONEXISTENT9999") is None


def test_index_meta_sidecar_present(populated_index):
    """``index_meta`` reads the JSON sidecar after a build."""
    meta = nxp_monkey.index_meta()
    assert meta is not None
    assert meta.part_count == populated_index.part_count
