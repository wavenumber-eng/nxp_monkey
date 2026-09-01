"""Deterministic acquisition locks for NXP KEX processor data."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from ._version import __version__
from .kex_client import PROCESSORS_PATH, _storage_url
from .xml_json import xml_file_to_dict

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_XMLNS_RE = re.compile(r'xmlns(?::([\w.-]+))?\s*=\s*["\']([^"\']+)["\']')
_XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"


class KexLockError(RuntimeError):
    """Raised when KEX acquisition locking or verification fails."""


def canonical_kex_lock_bytes(value: object) -> bytes:
    """Return canonical JSON bytes for a KEX acquisition lock."""
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def kex_lock_id(lock: dict[str, object]) -> str:
    """Compute the content identity of a KEX acquisition lock."""
    identity = dict(lock)
    identity.pop("lock_id", None)
    digest = hashlib.sha256(canonical_kex_lock_bytes(identity)).hexdigest()
    return f"sha256:{digest}"


def create_kex_acquisition_lock(
    *,
    part: str,
    upstream_version: str,
    variants: list[str] | tuple[str, ...],
    cache_dir: str | Path,
    output: str | Path,
    producer_revision: str,
) -> dict[str, object]:
    """Bind retained archives, extracted files, schemas, and JSON mirrors."""
    canonical_part = part.strip().upper()
    selected_variants = sorted(set(variants))
    if not canonical_part or not selected_variants:
        raise KexLockError("part and at least one variant are required")
    if not _COMMIT_RE.fullmatch(producer_revision):
        raise KexLockError("producer revision must be an exact lowercase Git commit")

    version_root = Path(cache_dir).resolve() / "kex" / upstream_version
    archives, raw_files, mirror_files, schemas = _collect_inputs(
        version_root, canonical_part, upstream_version, selected_variants
    )

    variant_args = [item for variant in selected_variants for item in ("--variant", variant)]
    lock: dict[str, object] = {
        "$schema": "https://wavenumber.dev/nxp-monkey/kex-acquisition-lock.schema.v1.json",
        "archives": archives,
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v1",
        "json_mirror": {
            "algorithm": "nxp_monkey.xml_json.xml_file_to_dict-json-indent-2-v1",
            "files": mirror_files,
        },
        "policy": {
            "ai_assisted_generation": "allowed",
            "raw_input_redistribution": "not-granted-by-this-lock",
            "retention": "local-cache",
        },
        "producer": {
            "command": [
                "nxp-monkey", "kex-lock", "create", "--part", canonical_part,
                "--upstream-version", upstream_version, *variant_args,
                "--producer-revision", producer_revision, "--cache", "${CACHE}",
                "--output", "${OUTPUT}",
            ],
            "name": "nxp-monkey",
            "repository": "https://github.com/wavenumber-eng/nxp_monkey",
            "revision": producer_revision,
            "version": __version__,
        },
        "raw_files": raw_files,
        "request": {
            "part": canonical_part,
            "upstream_version": upstream_version,
            "variants": selected_variants,
        },
        "schema_version": "1",
        "schemas": schemas,
        "verification": {
            "command": [
                "nxp-monkey", "kex-lock", "verify", "--lock", "${LOCK}",
                "--cache", "${CACHE}", "--offline",
            ]
        },
    }
    lock["lock_id"] = kex_lock_id(lock)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_kex_lock_bytes(lock))
    return lock


def _collect_inputs(
    version_root: Path, part: str, version: str, variants: list[str]
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, str]],
]:
    """Collect deterministic records for every retained input and mirror."""
    archives: list[dict[str, object]] = []
    raw_files: list[dict[str, object]] = []
    mirrors: list[dict[str, object]] = []
    schemas: dict[tuple[str, str, str], dict[str, str]] = {}
    for variant in variants:
        archive = version_root / "_zips" / f"{part}_{variant}.zip"
        tree = version_root / "processors" / part / variant
        if not archive.is_file():
            raise KexLockError(f"missing retained archive: {archive}")
        if not tree.is_dir():
            raise KexLockError(f"missing extracted variant tree: {tree}")
        archives.append(_archive_record(archive, part, variant, version))
        for source in sorted(path for path in tree.rglob("*") if path.is_file()):
            relative = source.relative_to(version_root).as_posix()
            raw_files.append(_file_record(source, relative, variant))
            if source.suffix.lower() != ".xml":
                continue
            mirrors.append(_json_mirror_record(source, relative, variant))
            schema = _xml_schema_identity(source)
            key = (schema["namespace"], schema["root"], schema["schema_location"])
            schemas[key] = schema
    ordered_schemas = sorted(schemas.values(), key=lambda item: tuple(item.values()))
    return archives, raw_files, mirrors, ordered_schemas


def _archive_record(archive: Path, part: str, variant: str, version: str) -> dict[str, object]:
    """Return the immutable record for one retained upstream archive."""
    return {
        "path": f"_zips/{archive.name}",
        "sha256": _sha256_path(archive),
        "size": archive.stat().st_size,
        "url": _storage_url(
            version,
            f"{PROCESSORS_PATH}/{part}/{variant}",
            "getdir",
            {"recursive": "true"},
        ),
        "variant": variant,
    }


def verify_kex_acquisition_lock(
    *, lock: str | Path | dict[str, object], cache_dir: str | Path, offline: bool
) -> dict[str, object]:
    """Verify a KEX acquisition lock solely against retained local bytes."""
    if not offline:
        raise KexLockError("KEX lock verification must be explicitly offline")
    payload = _load_lock(lock)
    _validate_lock(payload)
    expected_id = kex_lock_id(payload)
    if payload.get("lock_id") != expected_id:
        raise KexLockError(f"lock ID mismatch: expected {expected_id}")

    request = _mapping(payload["request"], "request")
    part = _text(request["part"], "request.part")
    version = _text(request["upstream_version"], "request.upstream_version")
    variants = _string_list(request["variants"], "request.variants")
    archives = _records(payload["archives"], "archives")
    raw_files = _records(payload["raw_files"], "raw_files")
    version_root = Path(cache_dir).resolve() / "kex" / version
    expected_archives = {_text(item["path"], "archive.path") for item in archives}
    actual_archives = {
        path.relative_to(version_root).as_posix()
        for path in (version_root / "_zips").glob(f"{part}_*.zip")
        if path.name.removeprefix(f"{part}_").removesuffix(".zip") in variants
    }
    _require_same_paths("archives", expected_archives, actual_archives)
    for record in archives:
        _verify_file(version_root / _text(record["path"], "archive.path"), record)

    roots = [version_root / "processors" / part / variant for variant in variants]
    actual_raw = {
        path.relative_to(version_root).as_posix()
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
    }
    expected_raw = {_text(item["path"], "raw_file.path") for item in raw_files}
    _require_same_paths("raw files", expected_raw, actual_raw)
    for record in raw_files:
        _verify_file(version_root / _text(record["path"], "raw_file.path"), record)

    actual_mirrors: list[dict[str, object]] = []
    actual_schemas: dict[tuple[str, str, str], dict[str, str]] = {}
    for source_path in sorted(path for root in roots for path in root.rglob("*.xml")):
        relative = source_path.relative_to(version_root).as_posix()
        variant = source_path.relative_to(version_root / "processors" / part).parts[0]
        actual_mirrors.append(_json_mirror_record(source_path, relative, variant))
        schema = _xml_schema_identity(source_path)
        actual_schemas[(schema["namespace"], schema["root"], schema["schema_location"])] = schema
    mirror = _mapping(payload["json_mirror"], "json_mirror")
    if actual_mirrors != _records(mirror["files"], "json_mirror.files"):
        raise KexLockError("regenerated JSON mirror hashes differ from the lock")
    actual_schema_list = sorted(actual_schemas.values(), key=lambda item: tuple(item.values()))
    if actual_schema_list != payload["schemas"]:
        raise KexLockError("XML schema identities differ from the lock")
    return {
        "archives_verified": len(archives),
        "files_verified": len(raw_files),
        "json_mirrors_verified": len(actual_mirrors),
        "lock_id": expected_id,
        "offline": True,
    }


def _file_record(path: Path, relative: str, variant: str) -> dict[str, object]:
    return {
        "path": relative,
        "sha256": _sha256_path(path),
        "size": path.stat().st_size,
        "variant": variant,
    }


def _json_mirror_record(path: Path, relative: str, variant: str) -> dict[str, object]:
    data = (json.dumps(xml_file_to_dict(path), indent=2, ensure_ascii=False) + "\n").encode()
    target = str(Path(relative).with_suffix(".json")).replace("\\", "/")
    return {"path": target, "sha256": hashlib.sha256(data).hexdigest(), "variant": variant}


def _xml_schema_identity(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise KexLockError(f"cannot parse XML schema identity for {path}: {exc}") from exc
    namespace = root.tag[1:].partition("}")[0] if root.tag.startswith("{") else ""
    local = root.tag.rpartition("}")[2]
    declarations = sorted(match.group(2) for match in _XMLNS_RE.finditer(text[:16384]))
    return {
        "namespace": namespace,
        "namespace_declarations": " ".join(declarations),
        "root": local,
        "schema_location": root.attrib.get(_XSI_SCHEMA_LOCATION, ""),
    }


def _load_lock(lock: str | Path | dict[str, object]) -> dict[str, object]:
    if isinstance(lock, (str, Path)):
        try:
            return cast(dict[str, object], json.loads(Path(lock).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise KexLockError(f"cannot read KEX lock: {exc}") from exc
    return lock


def _validate_lock(lock: dict[str, object]) -> None:
    required = {
        "archives", "canonicalization", "json_mirror", "lock_id", "policy",
        "producer", "raw_files", "request", "schema_version", "schemas", "verification",
    }
    missing = sorted(required - lock.keys())
    if missing:
        raise KexLockError(f"KEX lock is missing fields: {missing}")
    if lock["schema_version"] != "1":
        raise KexLockError("unsupported KEX lock schema version")
    if not lock["archives"] or not lock["raw_files"]:
        raise KexLockError("KEX lock must bind archives and raw files")
    producer = _mapping(lock["producer"], "producer")
    if not _COMMIT_RE.fullmatch(_text(producer.get("revision"), "producer.revision")):
        raise KexLockError("KEX lock producer revision is not an exact commit")


def _verify_file(path: Path, record: dict[str, object]) -> None:
    if not path.is_file():
        raise KexLockError(f"missing locked file: {record['path']}")
    if path.stat().st_size != record["size"] or _sha256_path(path) != record["sha256"]:
        raise KexLockError(f"locked file differs: {record['path']}")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise KexLockError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _records(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise KexLockError(f"{label} must be an array")
    return [_mapping(item, label) for item in value]


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise KexLockError(f"{label} must be a string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise KexLockError(f"{label} must be an array of strings")
    return cast(list[str], value)


def _require_same_paths(label: str, expected: set[str], actual: set[str]) -> None:
    if expected != actual:
        detail = f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        raise KexLockError(
            f"{label} differ; {detail}"
        )


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
