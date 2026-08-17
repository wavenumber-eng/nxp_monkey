"""Resolve and verify immutable locks for official MCUXpresso Git sources.

The v0 implementation deliberately excludes KEX data.  It delegates manifest
imports and board selection to west and the pinned manifest's own extension,
then stores only exact Git identities and explicitly licensed file hashes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from west.manifest import Manifest, Project

from ._version import __version__

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HTTPS_RE = re.compile(r"^https://")
_PROFILE_PLACEHOLDER = "${PROFILE}"


class SourceLockError(RuntimeError):
    """Raised when source resolution or immutable-lock verification fails."""


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical v0 JSON bytes for *value*."""
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def source_lock_id(lock: dict) -> str:
    """Compute the content ID for a source-lock record."""
    identity = dict(lock)
    identity.pop("lock_id", None)
    return f"sha256:{hashlib.sha256(canonical_json_bytes(identity)).hexdigest()}"


def resolve_source_lock(
    *,
    manifest_url: str,
    manifest_revision: str,
    board: str,
    device: str,
    profile_spec: str | Path,
    cache_dir: str | Path,
    output: str | Path,
    resolver_revision: str,
    kex_policy: str = "not-used",
) -> dict:
    """Resolve official sources and write a deterministic source lock.

    Args:
        manifest_url: HTTPS URL of the official MCUXpresso manifest repository.
        manifest_revision: Exact 40-character lowercase Git commit.
        board: Board identifier understood by the pinned ``update_board`` command.
        device: Exact uppercase device identifier retained in the lock.
        profile_spec: Reviewed JSON specification of consumed paths and licenses.
        cache_dir: Content cache used for online resolution and later replay.
        output: Destination JSON path.
        resolver_revision: Exact Git commit of the resolver implementation.
        kex_policy: Must be ``not-used`` for source-lock v0.

    Returns:
        The source-lock mapping that was written.
    """
    _validate_resolve_request(
        manifest_url, manifest_revision, resolver_revision, kex_policy
    )
    profile_path = Path(profile_spec).resolve()
    profile = _load_profile(profile_path)
    cache_root = Path(cache_dir).resolve() / "source-v0"
    cache_root.mkdir(parents=True, exist_ok=True)
    manifest_git = _ensure_repository_commit(
        manifest_url, manifest_revision, cache_root, online=True, partial=False
    )
    state = _resolve_manifest_state(
        manifest_git, manifest_revision, board, profile, profile_path, cache_root
    )
    profile_record = _cache_profile(profile_path, profile["canonical_path"], cache_root)
    lock = _build_source_lock(
        manifest_url=manifest_url,
        manifest_revision=manifest_revision,
        board=board,
        device=device,
        resolver_revision=resolver_revision,
        profile=profile,
        profile_record=profile_record,
        state=state,
    )
    _validate_lock_semantics(lock)
    lock["lock_id"] = source_lock_id(lock)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_json_bytes(lock))
    return lock


def _validate_resolve_request(
    manifest_url: str,
    manifest_revision: str,
    resolver_revision: str,
    kex_policy: str,
) -> None:
    _require_exact_commit(manifest_revision, "manifest revision")
    _require_exact_commit(resolver_revision, "resolver revision")
    if not _HTTPS_RE.match(manifest_url):
        raise SourceLockError("manifest URL must use HTTPS")
    if kex_policy != "not-used":
        raise SourceLockError("source-lock v0 permits only --kex-policy not-used")


def _resolve_manifest_state(
    manifest_git: Path,
    manifest_revision: str,
    board: str,
    profile: dict,
    profile_path: Path,
    cache_root: Path,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="nxp-monkey-source-") as temporary:
        workspace = _prepare_manifest_workspace(
            Path(temporary), manifest_git, manifest_revision
        )
        resolution_output, selection_output = _run_manifest_oracles(workspace, board)
        try:
            from west.manifest import Manifest
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise SourceLockError(
                "source resolution requires the 'source' extra (west==1.5.0)"
            ) from exc
        manifest = Manifest.from_topdir(topdir=workspace)
        selection = _load_yaml(selection_output, "board selection output")
        if not selection:
            raise SourceLockError("board selection output must be a non-empty mapping")
        projects, selected_names = _project_inventory(
            manifest, selection, set(profile["optional_projects"])
        )
        _fetch_selected_projects(projects, cache_root)
        west_yml = _git_blob(manifest_git, manifest_revision, "west.yml")
        board_path = f"boards/{board}.yml"
        board_config = _git_blob(manifest_git, manifest_revision, board_path)
        imports = _collect_local_imports(manifest_git, manifest_revision, west_yml)
        consumed_inputs = _resolve_consumed_inputs(
            profile["consumed_sources"], projects, cache_root
        )
        policy = _cache_license_policy(profile_path, profile["license_policy"], cache_root)
        return {
            "board_config": board_config,
            "board_path": board_path,
            "consumed_inputs": consumed_inputs,
            "group_filter": list(manifest.group_filter),
            "imports": imports,
            "policy": policy,
            "projects": sorted(projects, key=lambda item: (item["name"], item["path"])),
            "resolution_output": resolution_output,
            "selected_names": selected_names,
            "selection_output": selection_output,
            "west_yml": west_yml,
        }


def _prepare_manifest_workspace(
    temporary: Path, manifest_git: Path, manifest_revision: str
) -> Path:
    workspace = temporary / "workspace"
    manifest_checkout = workspace / "manifests"
    workspace.mkdir(parents=True)
    _run_git(["clone", "--shared", "--no-checkout", str(manifest_git), str(manifest_checkout)])
    _run_git(["-C", str(manifest_checkout), "checkout", "--detach", manifest_revision])
    _run_west(["init", "-l", "manifests"], cwd=workspace)
    return workspace


def _run_manifest_oracles(workspace: Path, board: str) -> tuple[str, str]:
    resolution = _run_west(
        ["manifest", "--resolve", "--active-only"], cwd=workspace, capture=True
    )
    selection = _run_west(
        ["-q", "update_board", "--set", "board", board, "--list-repo"],
        cwd=workspace,
        capture=True,
    )
    return _normalize_text(resolution), _normalize_text(selection)


def _project_inventory(
    manifest: Manifest, selection: dict, optional_names: set[str]
) -> tuple[list[dict], set[str]]:
    projects = list(manifest.projects[1:])
    selected_names = set(selection) | optional_names
    unknown = selected_names - {project.name for project in projects}
    if unknown:
        raise SourceLockError(f"selection references unknown projects: {sorted(unknown)}")
    records = [
        _project_record(project, manifest, selection, selected_names, optional_names)
        for project in projects
    ]
    return records, selected_names


def _project_record(
    project: Project,
    manifest: Manifest,
    selection: dict,
    selected_names: set[str],
    optional_names: set[str],
) -> dict:
    active = bool(manifest.is_active(project))
    selected = project.name in selected_names
    optional = _project_is_optional(project.name, selection, selected, optional_names)
    exact = bool(_COMMIT_RE.fullmatch(project.revision))
    resolved_commit = project.revision if exact else None
    _validate_project_availability(project, active, selected, optional, resolved_commit)
    return {
        "active": active,
        "groups": sorted(project.groups),
        "manifest_revision": project.revision,
        "name": project.name,
        "optional": optional,
        "path": project.path.rstrip("/"),
        "resolution_status": "resolved" if exact else "unavailable-inactive",
        "resolved_commit": resolved_commit,
        "selected": selected,
        "url": project.url,
    }


def _project_is_optional(
    name: str, selection: dict, selected: bool, optional_names: set[str]
) -> bool:
    selection_record = selection.get(name)
    oracle_optional = isinstance(selection_record, dict) and bool(
        selection_record.get("optional", False)
    )
    return name in optional_names or (selected and oracle_optional)


def _validate_project_availability(
    project: Project,
    active: bool,
    selected: bool,
    optional: bool,
    resolved_commit: str | None,
) -> None:
    if resolved_commit is None and (active or selected):
        raise SourceLockError(
            f"active or selected project {project.name!r} has moving revision "
            f"{project.revision!r}; source-lock v0 requires an exact commit"
        )
    if selected and (not active and not optional):
        raise SourceLockError(f"selected project {project.name!r} is unavailable")


def _fetch_selected_projects(projects: list[dict], cache_root: Path) -> None:
    commits = {
        (project["url"], project["resolved_commit"])
        for project in projects
        if project["selected"]
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(_ensure_repository_commit, url, commit, cache_root, True)
            for url, commit in sorted(commits)
        ]
        for future in futures:
            future.result()


def _cache_profile(path: Path, canonical_path: str, cache_root: Path) -> dict:
    data = path.read_bytes()
    record = {"path": canonical_path, "sha256": _sha256(data)}
    _cache_bytes(cache_root / "profiles" / record["sha256"], data)
    return record


def _build_source_lock(
    *,
    manifest_url: str,
    manifest_revision: str,
    board: str,
    device: str,
    resolver_revision: str,
    profile: dict,
    profile_record: dict,
    state: dict,
) -> dict:
    projects = state["projects"]
    return {
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "closures": {
            "board_reference_projects": sorted(state["selected_names"]),
            "consumed_inputs": state["consumed_inputs"],
            "optional_projects": sorted(
                item["name"] for item in projects if item["optional"]
            ),
        },
        "kex": {
            "license_evidence": sorted(profile["kex"]["license_evidence"]),
            "reason": profile["kex"]["reason"],
            "status": "not-used",
        },
        "license_policy": state["policy"],
        "manifest": _manifest_record(manifest_url, manifest_revision, profile, state),
        "projects": projects,
        "request": {"board": board, "device": device, "profile": profile["profile"]},
        "resolver": _resolver_record(
            manifest_url,
            manifest_revision,
            board,
            device,
            resolver_revision,
            profile_record,
        ),
        "schema_version": "0",
    }


def _manifest_record(
    manifest_url: str, manifest_revision: str, profile: dict, state: dict
) -> dict:
    resolution = state["resolution_output"]
    selection = state["selection_output"]
    return {
        "board_config": {
            "path": state["board_path"],
            "sha256": _sha256(state["board_config"]),
        },
        "commit": manifest_revision,
        "freeze_output": resolution,
        "freeze_output_sha256": _sha256(resolution.encode()),
        "group_filter": state["group_filter"],
        "imports": state["imports"],
        "label": profile["manifest_label"],
        "selection_output": selection,
        "selection_output_sha256": _sha256(selection.encode()),
        "url": manifest_url,
        "west_yml": {"path": "west.yml", "sha256": _sha256(state["west_yml"])},
    }


def _resolver_record(
    manifest_url: str,
    manifest_revision: str,
    board: str,
    device: str,
    resolver_revision: str,
    profile_record: dict,
) -> dict:
    return {
        "freeze_command": ["west", "manifest", "--resolve", "--active-only"],
        "name": "nxp-monkey",
        "profile_spec": profile_record,
        "resolve_command": [
            "nxp-monkey", "source", "resolve",
            "--manifest-url", manifest_url,
            "--manifest-revision", manifest_revision,
            "--board", board,
            "--device", device,
            "--profile-spec", _PROFILE_PLACEHOLDER,
            "--resolver-revision", resolver_revision,
            "--kex-policy", "not-used",
            "--cache", "${CACHE}",
            "--output", "${OUTPUT}",
        ],
        "revision": resolver_revision,
        "selection_command": [
            "west", "-q", "update_board", "--set", "board", board, "--list-repo"
        ],
        "verify_command": [
            "nxp-monkey", "source", "verify", "--cache", "${CACHE}",
            "--offline", "--lock", "${LOCK}",
        ],
        "version": __version__,
        "west_version": _west_version(),
    }


def verify_source_lock(
    *, lock: str | Path | dict, cache_dir: str | Path, offline: bool
) -> dict:
    """Verify a source lock and every cached commit/blob without network access."""
    if not offline:
        raise SourceLockError("v0 verification must be explicitly offline")
    payload = _load_lock(lock)
    _validate_lock_semantics(payload)
    expected_id = source_lock_id(payload)
    if payload.get("lock_id") != expected_id:
        raise SourceLockError(
            f"lock ID mismatch: expected {expected_id}, got {payload.get('lock_id')!r}"
        )
    cache_root = Path(cache_dir).resolve() / "source-v0"
    _verify_cached_record(cache_root / "profiles", payload["resolver"]["profile_spec"])
    _verify_cached_record(cache_root / "policies", payload["license_policy"])
    _verify_manifest_cache(payload, cache_root)
    _verify_project_cache(payload, cache_root)
    return {
        "lock_id": expected_id,
        "projects_verified": len(payload["closures"]["board_reference_projects"]),
        "inputs_verified": len(payload["closures"]["consumed_inputs"]),
        "offline": True,
    }


def _load_lock(lock: str | Path | dict) -> dict:
    if isinstance(lock, (str, Path)):
        return cast(dict, json.loads(Path(lock).read_text(encoding="utf-8")))
    return lock


def _verify_cached_record(directory: Path, record: dict) -> None:
    cached = directory / record["sha256"]
    if not cached.is_file() or _sha256(cached.read_bytes()) != record["sha256"]:
        raise SourceLockError(f"missing or corrupt cached record: {record['sha256']}")


def _verify_manifest_cache(payload: dict, cache_root: Path) -> None:
    manifest = payload["manifest"]
    manifest_git = _repository_path(cache_root, manifest["url"])
    _require_cached_commit(manifest_git, manifest["commit"])
    _verify_blob_hash(manifest_git, manifest["commit"], manifest["west_yml"])
    _verify_blob_hash(manifest_git, manifest["commit"], manifest["board_config"])
    for imported in manifest["imports"]:
        _verify_blob_hash(manifest_git, imported["owner_commit"], imported)
    _verify_embedded_hash(manifest, "selection_output")
    _verify_embedded_hash(manifest, "freeze_output")
    _verify_manifest_oracle(
        manifest_git,
        manifest["commit"],
        payload["request"]["board"],
        manifest["freeze_output"],
        manifest["selection_output"],
    )


def _verify_project_cache(payload: dict, cache_root: Path) -> None:
    project_map = {project["name"]: project for project in payload["projects"]}
    for project in payload["projects"]:
        if project["selected"]:
            repo = _repository_path(cache_root, project["url"])
            _require_cached_commit(repo, project["resolved_commit"])
    for consumed in payload["closures"]["consumed_inputs"]:
        project = project_map[consumed["project"]]
        repo = _repository_path(cache_root, project["url"])
        _verify_blob_hash(repo, project["resolved_commit"], consumed)


def _load_profile(path: Path) -> dict:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceLockError(f"cannot read profile specification {path}: {exc}") from exc
    profile = cast(object, profile)
    if not isinstance(profile, dict):
        raise SourceLockError("profile specification must be a JSON object")
    _validate_profile(profile)
    return profile


def _validate_profile(profile: dict) -> None:
    required = {
        "schema_version",
        "canonical_path",
        "profile",
        "license_policy",
        "kex",
        "optional_projects",
        "consumed_sources",
    }
    missing = sorted(required - profile.keys())
    if missing:
        raise SourceLockError(f"profile specification is missing {missing}")
    if profile["schema_version"] != "0" or not profile["consumed_sources"]:
        raise SourceLockError("profile v0 requires at least one consumed input")
    if profile["kex"].get("status") != "not-used":
        raise SourceLockError("profile v0 must record KEX as not-used")
    if not _is_unique_list(profile["optional_projects"]):
        raise SourceLockError("profile optional_projects must be a unique list")
    for source_set in profile["consumed_sources"]:
        _validate_source_set(source_set)


def _validate_source_set(source_set: object) -> None:
    expected = {
        "disposition",
        "license_evidence",
        "license_expression",
        "paths",
        "project",
    }
    if not isinstance(source_set, dict) or set(source_set) != expected:
        raise SourceLockError("profile consumed source set has invalid fields")
    if not source_set["paths"] or not _is_unique_list(source_set["paths"]):
        raise SourceLockError("profile source paths must be a non-empty unique list")


def _is_unique_list(value: object) -> bool:
    return isinstance(value, list) and len(value) == len(set(value))


def _cache_license_policy(
    profile_path: Path, policy: dict, cache_root: Path
) -> dict[str, str]:
    required = {"id", "revision", "path", "sha256"}
    if not isinstance(policy, dict) or required - policy.keys():
        raise SourceLockError("profile license_policy is incomplete")
    source = _find_ancestor_file(profile_path.parent, policy["path"])
    data = source.read_bytes()
    if _sha256(data) != policy["sha256"]:
        raise SourceLockError("license policy hash does not match the profile")
    target = cache_root / "policies" / policy["sha256"]
    _cache_bytes(target, data)
    return {key: policy[key] for key in sorted(required)}


def _find_ancestor_file(start: Path, relative: str) -> Path:
    for root in (start, *start.parents):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise SourceLockError(f"cannot locate retained policy {relative!r} above {start}")


def _resolve_consumed_inputs(
    source_sets: list[dict],
    projects: list[dict],
    cache_root: Path,
) -> list[dict]:
    project_map = {project["name"]: project for project in projects}
    resolved = []
    for source_set in source_sets:
        project_name = source_set.get("project")
        project = project_map.get(project_name)
        if project is None or not project["selected"] or project["resolved_commit"] is None:
            raise SourceLockError(f"consumed input project is not selected: {project_name!r}")
        disposition = source_set.get("disposition", {})
        for permission in ("redistribute", "generate", "ai_input"):
            if disposition.get(permission) != "allow":
                raise SourceLockError(
                    f"consumed source set {project_name!r} "
                    f"does not allow {permission}"
                )
        repo = _repository_path(cache_root, project["url"])
        for path in source_set["paths"]:
            data = _git_blob(repo, project["resolved_commit"], path)
            resolved.append(
                {
                    "disposition": disposition,
                    "license_evidence": sorted(source_set["license_evidence"]),
                    "license_expression": source_set["license_expression"],
                    "path": path,
                    "project": project_name,
                    "sha256": _sha256(data),
                }
            )
    return sorted(resolved, key=lambda item: (item["project"], item["path"]))


def _collect_local_imports(
    manifest_git: Path, commit: str, west_yml: bytes
) -> list[dict]:
    document = _load_yaml(west_yml.decode(), "west.yml")
    entries = document.get("manifest", {}).get("self", {}).get("import", [])
    if isinstance(entries, (str, dict)):
        entries = [entries]
    records: list[dict] = []
    for entry in entries:
        path = entry if isinstance(entry, str) else entry.get("file")
        if not isinstance(path, str):
            raise SourceLockError("v0 supports only local self-import paths")
        if path.endswith("/"):
            directory = path.rstrip("/")
            names = _git_lines(
                manifest_git, ["ls-tree", "-r", "--name-only", commit, "--", directory]
            )
            imported_paths = [name for name in names if name.endswith((".yml", ".yaml"))]
        else:
            directory = path
            imported_paths = [path]
        for imported_path in imported_paths:
            data = _git_blob(manifest_git, commit, imported_path)
            imported = _load_yaml(data.decode(), imported_path)
            nested_self = imported.get("manifest", {}).get("self", {}).get("import")
            nested_projects = any(
                "import" in project
                for project in imported.get("manifest", {}).get("projects", [])
                if isinstance(project, dict)
            )
            if nested_self or nested_projects:
                raise SourceLockError(
                    "nested/project imports require a source-lock schema revision"
                )
            chain = (
                ["west.yml", directory, imported_path]
                if path.endswith("/")
                else ["west.yml", imported_path]
            )
            records.append(
                {
                    "import_path": chain,
                    "owner_commit": commit,
                    "owner_project": "manifest",
                    "path": imported_path,
                    "sha256": _sha256(data),
                }
            )
    return sorted(records, key=lambda item: (item["owner_project"], item["path"]))


def _ensure_repository_commit(
    url: str,
    commit: str,
    cache_root: Path,
    online: bool,
    partial: bool = True,
) -> Path:
    _require_exact_commit(commit, "resolved commit")
    repository = _repository_path(cache_root, url)
    repository.parent.mkdir(parents=True, exist_ok=True)
    if not repository.exists():
        if not online:
            raise SourceLockError(f"repository is absent from offline cache: {url}")
        _run_git(["init", "--bare", str(repository)])
        _run_git(["--git-dir", str(repository), "remote", "add", "origin", url])
    has_commit = _has_commit(repository, commit)
    needs_full_refetch = not partial and has_commit and _has_missing_objects(repository, commit)
    if not has_commit or needs_full_refetch:
        if not online:
            raise SourceLockError(f"commit {commit} is absent from offline cache: {url}")
        fetch = ["--git-dir", str(repository), "fetch"]
        if partial:
            fetch.append("--filter=blob:none")
        else:
            fetch.append("--no-filter")
            if needs_full_refetch:
                fetch.append("--refetch")
        fetch.extend(
            ["--no-tags", "origin", f"+{commit}:refs/nxp-monkey/{commit}"]
        )
        _run_git(fetch)
    _require_cached_commit(repository, commit)
    return repository


def _repository_path(cache_root: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()
    return cache_root / "repositories" / f"{key}.git"


def _validate_lock_semantics(lock: dict) -> None:
    try:
        _validate_lock_header(lock)
        selected = _validate_projects(lock["projects"])
        _validate_closures(lock["closures"], lock["projects"], selected)
        _validate_lock_revisions(lock)
        _validate_command_templates(lock["resolver"])
    except (KeyError, TypeError) as exc:
        raise SourceLockError(f"malformed source lock: {exc}") from exc
    except ValueError as exc:
        raise SourceLockError(f"malformed source-lock command: {exc}") from exc


def _validate_lock_header(lock: dict) -> None:
    if lock["schema_version"] != "0" or lock["kex"]["status"] != "not-used":
        raise SourceLockError("unsupported source-lock or KEX mode")
    if lock["canonicalization"] != "json-sort-keys-indent-2-lf-final-newline-v0":
        raise SourceLockError("unsupported canonicalization")
    if set(lock["kex"]) != {"status", "license_evidence", "reason"}:
        raise SourceLockError("KEX not-used record cannot carry payload identity")


def _validate_projects(projects: list[dict]) -> set[str]:
    names = [project["name"] for project in projects]
    paths = [project["path"] for project in projects]
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        raise SourceLockError("project names and paths must be unique")
    if projects != sorted(projects, key=lambda item: (item["name"], item["path"])):
        raise SourceLockError("projects are not in canonical order")
    for project in projects:
        _validate_project(project)
    return {project["name"] for project in projects if project["selected"]}


def _validate_project(project: dict) -> None:
    if project["groups"] != sorted(set(project["groups"])):
        raise SourceLockError("project groups are not unique and canonically ordered")
    resolved = project["resolved_commit"]
    if project["selected"] or project["active"]:
        _require_exact_commit(resolved, f"project {project['name']} resolved commit")
        if project["resolution_status"] != "resolved":
            raise SourceLockError("active/selected projects must be resolved")
    elif resolved is None and project["resolution_status"] != "unavailable-inactive":
        raise SourceLockError("unresolved inactive project needs explicit status")
    if project["optional"] and not project["selected"]:
        raise SourceLockError("optional project flags are valid only in selected closure")


def _validate_closures(closures: dict, projects: list[dict], selected: set[str]) -> None:
    board_closure = set(closures["board_reference_projects"])
    if board_closure != selected:
        raise SourceLockError("board closure must equal selected projects")
    if closures["board_reference_projects"] != sorted(board_closure):
        raise SourceLockError("board closure is not in canonical order")
    optional = {project["name"] for project in projects if project["optional"]}
    if set(closures["optional_projects"]) != optional:
        raise SourceLockError("optional closure must equal optional project flags")
    if closures["optional_projects"] != sorted(optional):
        raise SourceLockError("optional closure is not in canonical order")
    _validate_consumed_inputs(closures["consumed_inputs"], selected)


def _validate_consumed_inputs(consumed_inputs: list[dict], selected: set[str]) -> None:
    if consumed_inputs != sorted(
        consumed_inputs, key=lambda item: (item["project"], item["path"])
    ):
        raise SourceLockError("consumed inputs are not in canonical order")
    seen: set[tuple[str, str]] = set()
    for consumed in consumed_inputs:
        key = (consumed["project"], consumed["path"])
        if key in seen or consumed["project"] not in selected:
            raise SourceLockError("consumed inputs must be unique and selected")
        seen.add(key)
        if any(
            consumed["disposition"][permission] != "allow"
            for permission in ("redistribute", "generate", "ai_input")
        ):
            raise SourceLockError("consumed input policy must fail closed")


def _validate_lock_revisions(lock: dict) -> None:
    if lock["manifest"]["commit"] is None:
        raise SourceLockError("manifest commit is required")
    _require_exact_commit(lock["manifest"]["commit"], "manifest commit")
    _require_exact_commit(lock["resolver"]["revision"], "resolver revision")
    if lock["resolver"]["west_version"] != "1.5.0":
        raise SourceLockError("source-lock v0 requires west 1.5.0")


def _validate_command_templates(resolver: dict) -> None:
    resolve_placeholders = {
        "--cache": "${CACHE}",
        "--output": "${OUTPUT}",
        "--profile-spec": "${PROFILE}",
    }
    _validate_command_placeholders(resolver["resolve_command"], resolve_placeholders)
    verify_placeholders = {"--cache": "${CACHE}", "--lock": "${LOCK}"}
    _validate_command_placeholders(resolver["verify_command"], verify_placeholders)


def _validate_command_placeholders(command: list[str], placeholders: dict[str, str]) -> None:
    for option, expected in placeholders.items():
        index = command.index(option)
        if command[index + 1] != expected:
            raise SourceLockError(f"{option} must use canonical placeholder {expected}")


def _require_exact_commit(value: object, label: str) -> None:
    if not isinstance(value, str) or not _COMMIT_RE.fullmatch(value):
        raise SourceLockError(f"{label} must be an exact lowercase 40-hex commit")


def _require_cached_commit(repository: Path, commit: str) -> None:
    if not repository.is_dir() or not _has_commit(repository, commit):
        raise SourceLockError(f"missing cached commit {commit} in {repository}")


def _has_commit(repository: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "--git-dir", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        env=_git_environment(offline=True),
    )
    return result.returncode == 0


def _has_missing_objects(repository: Path, commit: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "--git-dir",
            str(repository),
            "rev-list",
            "--objects",
            "--missing=print",
            commit,
        ],
        capture_output=True,
        text=True,
        env=_git_environment(offline=True),
    )
    if result.returncode:
        raise SourceLockError(result.stderr.strip())
    return any(line.startswith("?") for line in result.stdout.splitlines())


def _verify_blob_hash(repository: Path, commit: str, record: dict) -> None:
    data = _git_blob(repository, commit, record["path"], offline=True)
    actual = _sha256(data)
    if actual != record["sha256"]:
        raise SourceLockError(
            f"blob hash mismatch for {record['path']}: {actual} != {record['sha256']}"
        )


def _verify_embedded_hash(record: dict, field: str) -> None:
    actual = _sha256(record[field].encode())
    expected = record[f"{field}_sha256"]
    if actual != expected:
        raise SourceLockError(f"embedded {field} hash mismatch: {actual} != {expected}")


def _git_blob(repository: Path, commit: str, path: str, offline: bool = False) -> bytes:
    result = subprocess.run(
        ["git", "--git-dir", str(repository), "show", f"{commit}:{path}"],
        capture_output=True,
        env=_git_environment(offline=offline),
    )
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise SourceLockError(f"cannot read {commit}:{path}: {message}")
    return result.stdout


def _git_lines(repository: Path, arguments: list[str]) -> list[str]:
    output = _run_git(["--git-dir", str(repository), *arguments], capture=True)
    return [line for line in output.splitlines() if line]


def _run_git(arguments: list[str], capture: bool = False, offline: bool = False) -> str:
    result = subprocess.run(
        ["git", *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_git_environment(offline=offline),
    )
    if result.returncode:
        raise SourceLockError(result.stderr.strip() or result.stdout.strip())
    return result.stdout if capture else ""


def _run_west(
    arguments: list[str], cwd: Path, capture: bool = False, offline: bool = False
) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "west", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_west_environment(offline=offline),
    )
    if result.returncode:
        raise SourceLockError(result.stderr.strip() or result.stdout.strip())
    return result.stdout if capture else ""


def _git_environment(*, offline: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    if offline:
        environment["GIT_NO_LAZY_FETCH"] = "1"
        environment["GIT_CONFIG_COUNT"] = "1"
        environment["GIT_CONFIG_KEY_0"] = "remote.origin.promisor"
        environment["GIT_CONFIG_VALUE_0"] = "false"
    return environment


def _west_environment(*, offline: bool = False) -> dict[str, str]:
    environment = _git_environment(offline=offline)
    environment.update({"NO_COLOR": "1", "PYTHONUTF8": "1"})
    return environment


def _west_version() -> str:
    try:
        from west.version import __version__ as west_version
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise SourceLockError("west is unavailable; install nxp-monkey[source]") from exc
    if west_version != "1.5.0":
        raise SourceLockError(f"source-lock v0 requires west 1.5.0, got {west_version}")
    return west_version


def _load_yaml(text: str, label: str) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise SourceLockError(f"cannot parse {label}: {exc}") from exc
    try:
        return cast(dict, yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise SourceLockError(f"cannot parse {label}: {exc}") from exc


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise SourceLockError(f"content-addressed cache collision at {target}")
        return
    target.write_bytes(data)


def _verify_manifest_oracle(
    manifest_git: Path,
    commit: str,
    board: str,
    expected_resolution: str,
    expected_selection: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="nxp-monkey-verify-") as temporary:
        workspace = Path(temporary) / "workspace"
        checkout = workspace / "manifests"
        workspace.mkdir(parents=True)
        _run_git(
            ["clone", "--shared", "--no-checkout", str(manifest_git), str(checkout)],
            offline=True,
        )
        _run_git(
            ["-C", str(checkout), "checkout", "--detach", commit], offline=True
        )
        _run_west(["init", "-l", "manifests"], cwd=workspace, offline=True)
        resolution = _normalize_text(
            _run_west(
                ["manifest", "--resolve", "--active-only"],
                cwd=workspace,
                capture=True,
                offline=True,
            )
        )
        selection = _normalize_text(
            _run_west(
                ["-q", "update_board", "--set", "board", board, "--list-repo"],
                cwd=workspace,
                capture=True,
                offline=True,
            )
        )
    if resolution != expected_resolution:
        raise SourceLockError("offline west manifest replay differs from the lock")
    if selection != expected_selection:
        raise SourceLockError("offline update_board replay differs from the lock")
