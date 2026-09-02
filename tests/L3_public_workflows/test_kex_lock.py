"""Exercise deterministic KEX acquisition locking and offline verification."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import nxp_monkey
import pytest
from nxp_monkey.kex_lock import kex_lock_id


def _stage_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    version = cache / "kex" / "26.06"
    tree = version / "processors" / "MCXA156" / "ksdk2_0"
    tree.mkdir(parents=True)
    (tree / "MCXA156.xml").write_text(
        '<?xml version="1.0"?><processor '
        'xmlns="http://swtools.freescale.net/XSD/processor/2.0" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://swtools.freescale.net/XSD/processor/2.0 processor.xsd" '
        'part="MCXA156"/>',
        encoding="utf-8",
    )
    (tree / "processor.properties").write_text("version=26.06.20\n", encoding="utf-8")
    archive = version / "_zips" / "MCXA156_ksdk2_0.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(tree / "MCXA156.xml", "processors/MCXA156/ksdk2_0/MCXA156.xml")
        bundle.write(
            tree / "processor.properties",
            "processors/MCXA156/ksdk2_0/processor.properties",
        )
    return cache


def test_kex_lock_round_trip_is_deterministic_and_offline(tmp_path):
    """Two creations from the same retained bytes are identical and verify."""
    cache = _stage_cache(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    kwargs = {
        "part": "MCXA156",
        "upstream_version": "26.06",
        "variants": ["ksdk2_0"],
        "cache_dir": cache,
        "producer_revision": "a" * 40,
    }
    lock = nxp_monkey.create_kex_acquisition_lock(output=first, **kwargs)
    nxp_monkey.create_kex_acquisition_lock(output=second, **kwargs)
    assert first.read_bytes() == second.read_bytes()
    result = nxp_monkey.verify_kex_acquisition_lock(
        lock=lock, cache_dir=cache, offline=True
    )
    assert result["offline"] is True
    assert result["archives_verified"] == 1
    assert result["json_mirrors_verified"] == 1

    xml_root = cache / "kex" / "26.06" / "processors" / "MCXA156" / "ksdk2_0"
    json_root = tmp_path / "mirror"
    nxp_monkey.mirror_xml_tree_as_json(xml_root, json_root)
    written = json_root / "MCXA156.json"
    payload = json.loads(first.read_text(encoding="utf-8"))
    expected = next(
        item["sha256"]
        for item in payload["json_mirror"]["files"]
        if item["path"].endswith("/MCXA156.json")
    )
    assert hashlib.sha256(written.read_bytes()).hexdigest() == expected


def test_kex_lock_detects_tampering_and_requires_offline(tmp_path):
    """Changed retained bytes and non-offline verification fail closed."""
    cache = _stage_cache(tmp_path)
    output = tmp_path / "lock.json"
    lock = nxp_monkey.create_kex_acquisition_lock(
        part="MCXA156",
        upstream_version="26.06",
        variants=["ksdk2_0"],
        cache_dir=cache,
        output=output,
        producer_revision="b" * 40,
    )
    with pytest.raises(nxp_monkey.KexLockError, match="explicitly offline"):
        nxp_monkey.verify_kex_acquisition_lock(lock=lock, cache_dir=cache, offline=False)
    raw = cache / "kex" / "26.06" / "processors" / "MCXA156" / "ksdk2_0"
    (raw / "processor.properties").write_text("changed\n", encoding="utf-8")
    with pytest.raises(nxp_monkey.KexLockError, match="differs"):
        nxp_monkey.verify_kex_acquisition_lock(lock=output, cache_dir=cache, offline=True)


def test_kex_lock_content_id_covers_payload(tmp_path):
    """Changing lock content without recomputing its ID is rejected."""
    cache = _stage_cache(tmp_path)
    output = tmp_path / "lock.json"
    nxp_monkey.create_kex_acquisition_lock(
        part="MCXA156",
        upstream_version="26.06",
        variants=["ksdk2_0"],
        cache_dir=cache,
        output=output,
        producer_revision="c" * 40,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["request"]["part"] = "MCXA153"
    with pytest.raises(nxp_monkey.KexLockError, match="lock ID mismatch"):
        nxp_monkey.verify_kex_acquisition_lock(lock=payload, cache_dir=cache, offline=True)


def test_kex_lock_rejects_path_tokens_and_archive_tampering(tmp_path):
    """Requests cannot escape the cache and retained archives are rehashed."""
    cache = _stage_cache(tmp_path)
    with pytest.raises(nxp_monkey.KexLockError, match="unsafe path token"):
        nxp_monkey.create_kex_acquisition_lock(
            part="../MCXA156",
            upstream_version="26.06",
            variants=["ksdk2_0"],
            cache_dir=cache,
            output=tmp_path / "bad.json",
            producer_revision="d" * 40,
        )
    output = tmp_path / "lock.json"
    lock = nxp_monkey.create_kex_acquisition_lock(
        part="MCXA156",
        upstream_version="26.06",
        variants=["ksdk2_0"],
        cache_dir=cache,
        output=output,
        producer_revision="d" * 40,
    )
    archive = cache / "kex" / "26.06" / "_zips" / "MCXA156_ksdk2_0.zip"
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(nxp_monkey.KexLockError, match="locked file differs"):
        nxp_monkey.verify_kex_acquisition_lock(lock=lock, cache_dir=cache, offline=True)


def test_kex_lock_rejects_schema_shape_drift(tmp_path):
    """The qualified path/attribute/cardinality manifest is verified."""
    cache = _stage_cache(tmp_path)
    output = tmp_path / "lock.json"
    nxp_monkey.create_kex_acquisition_lock(
        part="MCXA156",
        upstream_version="26.06",
        variants=["ksdk2_0"],
        cache_dir=cache,
        output=output,
        producer_revision="e" * 40,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["schema_shapes"]["paths"][0]["attributes"].append("Q{}invented")
    payload["lock_id"] = kex_lock_id(payload)
    with pytest.raises(nxp_monkey.KexLockError, match="schema shapes differ"):
        nxp_monkey.verify_kex_acquisition_lock(lock=payload, cache_dir=cache, offline=True)
