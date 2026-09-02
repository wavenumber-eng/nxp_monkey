"""Deterministic acquisition locks for NXP KEX processor data."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from ._version import __version__
from .fetch import _zip_relpath
from .kex_client import PROCESSORS_PATH, _storage_url
from .kex_schema import build_xml_schema_shapes
from .xml_json import _projection_bytes, xml_file_to_dict

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
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
    _safe_token(canonical_part, "part")
    _safe_token(upstream_version, "upstream version")
    for variant in selected_variants:
        _safe_token(variant, "variant")

    version_root = Path(cache_dir).resolve() / "kex" / upstream_version
    archives, raw_files, mirror_files, schemas, schema_shapes = _collect_inputs(
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
        "schema_shapes": {
            "algorithm": "qualified-path-attributes-observed-cardinality-v1",
            "paths": schema_shapes,
        },
        "verification": {
            "command": [
                "nxp-monkey", "kex-lock", "verify", "--lock", "${LOCK}",
                "--cache", "${CACHE}", "--offline",
            ]
        },
    }
    lock["lock_id"] = kex_lock_id(lock)
    _validate_lock(lock)
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
    list[dict[str, object]],
]:
    """Collect deterministic records for every retained input and mirror."""
    archives: list[dict[str, object]] = []
    raw_files: list[dict[str, object]] = []
    mirrors: list[dict[str, object]] = []
    schemas: dict[tuple[str, str, str], dict[str, str]] = {}
    xml_inputs: list[tuple[Path, str, str]] = []
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
            xml_inputs.append((source, relative, variant))
            schema = _xml_schema_identity(source)
            key = (schema["namespace"], schema["root"], schema["schema_location"])
            schemas[key] = schema
    ordered_schemas = sorted(schemas.values(), key=lambda item: tuple(item.values()))
    return archives, raw_files, mirrors, ordered_schemas, build_xml_schema_shapes(xml_inputs)


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

    part, version_root, roots, archives, raw_files = _verify_locked_files(payload, cache_dir)

    actual_mirrors: list[dict[str, object]] = []
    actual_schemas: dict[tuple[str, str, str], dict[str, str]] = {}
    xml_inputs: list[tuple[Path, str, str]] = []
    for source_path in sorted(path for root in roots for path in root.rglob("*.xml")):
        relative = source_path.relative_to(version_root).as_posix()
        variant = source_path.relative_to(version_root / "processors" / part).parts[0]
        actual_mirrors.append(_json_mirror_record(source_path, relative, variant))
        xml_inputs.append((source_path, relative, variant))
        schema = _xml_schema_identity(source_path)
        actual_schemas[(schema["namespace"], schema["root"], schema["schema_location"])] = schema
    mirror = _mapping(payload["json_mirror"], "json_mirror")
    if actual_mirrors != _records(mirror["files"], "json_mirror.files"):
        raise KexLockError("regenerated JSON mirror hashes differ from the lock")
    actual_schema_list = sorted(actual_schemas.values(), key=lambda item: tuple(item.values()))
    if actual_schema_list != payload["schemas"]:
        raise KexLockError("XML schema identities differ from the lock")
    shapes = _mapping(payload["schema_shapes"], "schema_shapes")
    if build_xml_schema_shapes(xml_inputs) != _records(shapes["paths"], "schema_shapes.paths"):
        raise KexLockError("XML schema shapes differ from the lock")
    return {
        "archives_verified": len(archives),
        "files_verified": len(raw_files),
        "json_mirrors_verified": len(actual_mirrors),
        "lock_id": expected_id,
        "offline": True,
    }


def _verify_locked_files(
    payload: dict[str, object], cache_dir: str | Path
) -> tuple[
    str, Path, list[Path], list[dict[str, object]], list[dict[str, object]]
]:
    request = _mapping(payload["request"], "request")
    part = _text(request["part"], "request.part")
    version = _text(request["upstream_version"], "request.upstream_version")
    variants = _string_list(request["variants"], "request.variants")
    _safe_token(part, "request.part")
    _safe_token(version, "request.upstream_version")
    for variant in variants:
        _safe_token(variant, "request.variant")
    archives = _records(payload["archives"], "archives")
    raw_files = _records(payload["raw_files"], "raw_files")
    version_root = Path(cache_dir).resolve() / "kex" / version
    expected_archives = {_text(item["path"], "archive.path") for item in archives}
    _require_same_paths(
        "archives", expected_archives, _actual_archive_paths(version_root, part, variants)
    )
    for record in archives:
        _verify_file(_confined_path(version_root, record["path"], "archive.path"), record)
    roots = [version_root / "processors" / part / variant for variant in variants]
    actual_raw = {
        path.relative_to(version_root).as_posix()
        for root in roots for path in root.rglob("*") if path.is_file()
    }
    expected_raw = {_text(item["path"], "raw_file.path") for item in raw_files}
    _require_same_paths("raw files", expected_raw, actual_raw)
    for record in raw_files:
        _verify_file(_confined_path(version_root, record["path"], "raw_file.path"), record)
    for archive in archives:
        _verify_archive_contents(version_root, archive, raw_files)
    return part, version_root, roots, archives, raw_files


def _file_record(path: Path, relative: str, variant: str) -> dict[str, object]:
    return {
        "path": relative,
        "sha256": _sha256_path(path),
        "size": path.stat().st_size,
        "variant": variant,
    }


def _json_mirror_record(path: Path, relative: str, variant: str) -> dict[str, object]:
    data = _projection_bytes(xml_file_to_dict(path))
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
        "producer", "raw_files", "request", "schema_shapes", "schema_version", "schemas",
        "verification",
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
    _require_keys(producer, {"command", "name", "repository", "revision", "version"}, "producer")
    _string_list(producer["command"], "producer.command")
    request = _mapping(lock["request"], "request")
    _require_keys(request, {"part", "upstream_version", "variants"}, "request")
    _text(request["part"], "request.part")
    _text(request["upstream_version"], "request.upstream_version")
    _string_list(request["variants"], "request.variants")
    _validate_file_records(_records(lock["archives"], "archives"), "archives", size=True)
    _validate_file_records(_records(lock["raw_files"], "raw_files"), "raw_files", size=True)
    mirror = _mapping(lock["json_mirror"], "json_mirror")
    _require_keys(mirror, {"algorithm", "files"}, "json_mirror")
    _validate_file_records(_records(mirror["files"], "json_mirror.files"), "mirrors")
    for schema in _records(lock["schemas"], "schemas"):
        _require_keys(
            schema,
            {"namespace", "namespace_declarations", "root", "schema_location"},
            "schema identity",
        )
        for key in ("namespace", "namespace_declarations", "root", "schema_location"):
            _text(schema[key], f"schema.{key}")
    shapes = _mapping(lock["schema_shapes"], "schema_shapes")
    _require_keys(shapes, {"algorithm", "paths"}, "schema_shapes")
    for shape in _records(shapes["paths"], "schema_shapes.paths"):
        _validate_schema_shape(shape)
    policy = _mapping(lock["policy"], "policy")
    if policy != {
        "ai_assisted_generation": "allowed",
        "raw_input_redistribution": "not-granted-by-this-lock",
        "retention": "local-cache",
    }:
        raise KexLockError("KEX lock policy is not the v1 policy")


def _validate_file_records(
    records: list[dict[str, object]], label: str, *, size: bool = False
) -> None:
    if not records:
        raise KexLockError(f"{label} must not be empty")
    required = {"path", "sha256", "variant"} | ({"size"} if size else set())
    for record in records:
        _require_keys(record, required, label)
        _text(record["path"], f"{label}.path")
        digest = _text(record["sha256"], f"{label}.sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise KexLockError(f"{label}.sha256 must be lowercase SHA-256")
        _text(record["variant"], f"{label}.variant")
        if size:
            byte_size = record["size"]
            if not isinstance(byte_size, int) or byte_size < 0:
                raise KexLockError(f"{label}.size must be a non-negative integer")


def _validate_schema_shape(shape: dict[str, object]) -> None:
    required = {
        "attributes", "children", "files", "occurrences", "path", "text_modes", "variant"
    }
    _require_keys(shape, required, "schema shape")
    _string_list(shape["attributes"], "schema_shape.attributes")
    _string_list(shape["files"], "schema_shape.files")
    _string_list(shape["text_modes"], "schema_shape.text_modes")
    _text(shape["path"], "schema_shape.path")
    _text(shape["variant"], "schema_shape.variant")
    if not isinstance(shape["occurrences"], int) or shape["occurrences"] < 1:
        raise KexLockError("schema_shape.occurrences must be a positive integer")
    for child in _records(shape["children"], "schema_shape.children"):
        _require_keys(child, {"name", "observed_max", "observed_min"}, "child cardinality")
        _text(child["name"], "child.name")
        for key in ("observed_min", "observed_max"):
            count = child[key]
            if not isinstance(count, int) or count < 0:
                raise KexLockError(f"child.{key} must be a non-negative integer")


def _require_keys(value: dict[str, object], keys: set[str], label: str) -> None:
    missing = sorted(keys - value.keys())
    if missing:
        raise KexLockError(f"{label} is missing fields: {missing}")


def _actual_archive_paths(root: Path, part: str, variants: list[str]) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in (root / "_zips").glob(f"{part}_*.zip")
        if path.name.removeprefix(f"{part}_").removesuffix(".zip") in variants
    }


def _verify_file(path: Path, record: dict[str, object]) -> None:
    if not path.is_file():
        raise KexLockError(f"missing locked file: {record['path']}")
    if path.stat().st_size != record["size"] or _sha256_path(path) != record["sha256"]:
        raise KexLockError(f"locked file differs: {record['path']}")


def _verify_archive_contents(
    root: Path, archive: dict[str, object], raw_files: list[dict[str, object]]
) -> None:
    variant = _text(archive["variant"], "archive.variant")
    expected = {
        _text(item["path"], "raw_file.path"): _text(item["sha256"], "raw_file.sha256")
        for item in raw_files
        if item["variant"] == variant
    }
    actual: dict[str, str] = {}
    archive_path = _confined_path(root, archive["path"], "archive.path")
    with zipfile.ZipFile(archive_path) as bundle:
        for member in bundle.infolist():
            relative = _zip_relpath(member.filename)
            if relative is None or member.is_dir():
                continue
            with bundle.open(member) as handle:
                actual[relative.as_posix()] = hashlib.sha256(handle.read()).hexdigest()
    if actual != expected:
        raise KexLockError(f"archive content differs from extracted {variant} closure")


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


def _safe_token(value: str, label: str) -> None:
    if value in {".", ".."} or not _TOKEN_RE.fullmatch(value):
        raise KexLockError(f"{label} contains an unsafe path token")


def _confined_path(root: Path, value: object, label: str) -> Path:
    relative = _text(value, label)
    candidate_path = Path(relative)
    if candidate_path.is_absolute() or ".." in candidate_path.parts:
        raise KexLockError(f"{label} must be a confined relative path")
    candidate = (root / candidate_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise KexLockError(f"{label} escapes the selected cache root")
    return candidate


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
