from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from nxp_monkey import SourceLockError, resolve_source_lock, verify_source_lock
from nxp_monkey.source_lock import (
    _repository_path,
    _validate_lock_semantics,
    canonical_json_bytes,
    source_lock_id,
)


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _bare_repository(tmp_path: Path, name: str, files: dict[str, bytes]) -> tuple[Path, str]:
    work = tmp_path / f"{name}-work"
    bare = tmp_path / f"{name}.git"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "tests@example.invalid")
    _git(work, "config", "user.name", "Source Lock Tests")
    for relative, data in files.items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(work, "add", ".")
    _git(work, "commit", "-m", "fixture")
    commit = _git(work, "rev-parse", "HEAD")
    _git(tmp_path, "clone", "--bare", str(work), str(bare))
    return bare, commit


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_offline_fixture(tmp_path: Path) -> tuple[dict, Path]:
    source = b"/* SPDX-License-Identifier: BSD-3-Clause */\n#define VALUE 1\n"
    core_bare, core_commit = _bare_repository(
        tmp_path, "core", {"devices/MCXFIXTURE.h": source}
    )
    core_url = "https://example.invalid/core"
    west_yml = f"""manifest:
  remotes:
  - name: fixture
    url-base: https://example.invalid
  projects:
  - name: core
    remote: fixture
    path: mcuxsdk
    revision: {core_commit}
    groups:
    - core
  self:
    path: manifests
    west-commands: scripts/west_commands.yml
    import:
    - submanifests/base.yml
    - submanifests/extra.yml
""".encode()
    board_yml = b"repo_list:\n  - core\n"
    imported_yml = b"manifest:\n  projects: []\n"
    extension_yml = b"""west-commands:
- file: scripts/fixture.py
  commands:
  - name: update_board
    class: FixtureUpdateBoard
    help: fixture board selector
"""
    extension_py = b"""import os
import socket
import subprocess

from west.commands import WestCommand

def assert_offline_denied(operation):
    try:
        operation()
    except RuntimeError as exc:
        if 'offline network access denied' in str(exc):
            return
        raise
    raise RuntimeError('offline guard did not deny network operation')

class FixtureUpdateBoard(WestCommand):
    def __init__(self):
        super().__init__('update_board', 'fixture board selector', 'fixture board selector')

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name, help=self.help)
        parser.add_argument('--set', nargs=2, required=True)
        parser.add_argument('--list-repo', action='store_true')
        return parser

    def do_run(self, args, unknown_args=None):
        if os.environ.get('NXP_MONKEY_OFFLINE') == '1':
            assert_offline_denied(lambda: socket.getaddrinfo('example.com', 443))
            assert_offline_denied(
                lambda: subprocess.run(['curl', 'https://example.com'], check=False)
            )
        print('core:\\n  display_name: Core')
"""
    manifest_bare, manifest_commit = _bare_repository(
        tmp_path,
        "manifest",
        {
            "west.yml": west_yml,
            "boards/fixture.yml": board_yml,
            "scripts/west_commands.yml": extension_yml,
            "scripts/fixture.py": extension_py,
            "submanifests/base.yml": imported_yml,
            "submanifests/extra.yml": imported_yml,
        },
    )

    cache = tmp_path / "cache"
    cache_root = cache / "source-v0"
    manifest_url = "https://example.invalid/manifest.git"
    manifest_cache = _repository_path(cache_root, manifest_url)
    core_cache = _repository_path(cache_root, core_url)
    manifest_cache.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "--bare", str(manifest_bare), str(manifest_cache)], check=True)
    subprocess.run(["git", "clone", "--bare", str(core_bare), str(core_cache)], check=True)

    policy = b"# reviewed fixture policy\n"
    policy_sha = _sha(policy)
    policy_path = cache_root / "policies" / policy_sha
    policy_path.parent.mkdir(parents=True)
    policy_path.write_bytes(policy)
    disposition = {
        "ai_input": "allow",
        "cache": "allow",
        "generate": "allow",
        "redistribute": "allow",
        "retain": "allow",
    }
    license_evidence = ["core:devices/MCXFIXTURE.h#SPDX"]
    kex = {
        "license_evidence": ["policy#kex"],
        "reason": "KEX is excluded from this synthetic Git-only fixture.",
        "status": "not-used",
    }
    policy_record = {
        "id": "fixture-policy-v0",
        "path": "docs/research/fixture-policy.md",
        "revision": "fixture",
        "sha256": policy_sha,
    }
    profile = canonical_json_bytes(
        {
            "canonical_path": "source-locks/profiles/fixture.json",
            "consumed_sources": [
                {
                    "disposition": disposition,
                    "license_evidence": license_evidence,
                    "license_expression": "BSD-3-Clause",
                    "paths": ["devices/MCXFIXTURE.h"],
                    "project": "core",
                }
            ],
            "kex": kex,
            "license_policy": policy_record,
            "manifest_label": "fixture",
            "optional_projects": [],
            "profile": "pac-v0",
            "schema_version": "0",
        }
    )
    profile_sha = _sha(profile)
    profile_path = cache_root / "profiles" / profile_sha
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(profile)

    oracle_workspace = tmp_path / "oracle-workspace"
    oracle_workspace.mkdir()
    _git(oracle_workspace, "clone", str(manifest_bare), "manifests")
    subprocess.run(
        [sys.executable, "-m", "west", "init", "-l", "manifests"],
        cwd=oracle_workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    frozen = subprocess.run(
        [sys.executable, "-m", "west", "manifest", "--resolve", "--active-only"],
        cwd=oracle_workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.replace("\r\n", "\n").rstrip("\n") + "\n"
    selection = subprocess.run(
        [
            sys.executable,
            "-m",
            "west",
            "-q",
            "update_board",
            "--set",
            "board",
            "fixture",
            "--list-repo",
        ],
        cwd=oracle_workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.replace("\r\n", "\n").rstrip("\n") + "\n"
    lock = {
        "canonicalization": "json-sort-keys-indent-2-lf-final-newline-v0",
        "closures": {
            "board_reference_projects": ["core"],
            "consumed_inputs": [
                {
                    "disposition": disposition,
                    "license_evidence": license_evidence,
                    "license_expression": "BSD-3-Clause",
                    "path": "devices/MCXFIXTURE.h",
                    "project": "core",
                    "sha256": _sha(source),
                }
            ],
            "optional_projects": [],
        },
        "kex": kex,
        "license_policy": policy_record,
        "manifest": {
            "board_config": {"path": "boards/fixture.yml", "sha256": _sha(board_yml)},
            "commit": manifest_commit,
            "freeze_output": frozen,
            "freeze_output_sha256": _sha(frozen.encode()),
            "group_filter": [],
            "imports": [
                {
                    "import_path": ["west.yml", "submanifests/base.yml"],
                    "owner_commit": manifest_commit,
                    "owner_project": "manifest",
                    "path": "submanifests/base.yml",
                    "sha256": _sha(imported_yml),
                },
                {
                    "import_path": ["west.yml", "submanifests/extra.yml"],
                    "owner_commit": manifest_commit,
                    "owner_project": "manifest",
                    "path": "submanifests/extra.yml",
                    "sha256": _sha(imported_yml),
                }
            ],
            "label": "fixture",
            "selection_output": selection,
            "selection_output_sha256": _sha(selection.encode()),
            "url": manifest_url,
            "west_yml": {"path": "west.yml", "sha256": _sha(west_yml)},
        },
        "projects": [
            {
                "active": True,
                "groups": ["core"],
                "manifest_revision": core_commit,
                "name": "core",
                "optional": False,
                "path": "mcuxsdk",
                "resolved_commit": core_commit,
                "resolution_status": "resolved",
                "selected": True,
                "url": core_url,
            }
        ],
        "request": {"board": "fixture", "device": "MCXFIXTURE", "profile": "pac-v0"},
        "resolver": {
            "freeze_command": ["west", "manifest", "--resolve", "--active-only"],
            "name": "nxp-monkey",
            "profile_spec": {
                "path": "source-locks/profiles/fixture.json",
                "sha256": profile_sha,
            },
            "resolve_command": [
                "nxp-monkey",
                "source",
                "resolve",
                "--profile-spec",
                "${PROFILE}",
                "--cache",
                "${CACHE}",
                "--output",
                "${OUTPUT}",
            ],
            "revision": "1" * 40,
            "selection_command": ["west", "-q", "update_board"],
            "verify_command": [
                "nxp-monkey",
                "source",
                "verify",
                "--cache",
                "${CACHE}",
                "--offline",
                "--lock",
                "${LOCK}",
            ],
            "version": "0.0.0",
            "west_version": "1.5.0",
        },
        "schema_version": "0",
    }
    lock["lock_id"] = source_lock_id(lock)
    return lock, cache


@pytest.fixture(scope="module")
def offline_fixture(tmp_path_factory) -> tuple[dict, Path]:
    return _build_offline_fixture(tmp_path_factory.mktemp("source-lock-fixture"))


def test_offline_verification_checks_all_cached_content(offline_fixture, monkeypatch):
    lock, cache = offline_fixture
    real_run = subprocess.run

    def network_trap(arguments, *args, **kwargs):
        command = [str(argument) for argument in arguments]
        assert "fetch" not in command
        assert "ls-remote" not in command
        return real_run(arguments, *args, **kwargs)

    monkeypatch.setattr("nxp_monkey.source_lock.subprocess.run", network_trap)
    result = verify_source_lock(lock=lock, cache_dir=cache, offline=True)
    assert result == {
        "inputs_verified": 1,
        "lock_id": lock["lock_id"],
        "offline": True,
        "projects_verified": 1,
    }


def test_offline_verification_rejects_wrong_id(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["lock_id"] = "sha256:" + "0" * 64
    with pytest.raises(SourceLockError, match="lock ID mismatch"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_offline_verification_rejects_corrupt_blob_hash(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["closures"]["consumed_inputs"][0]["sha256"] = "0" * 64
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="consumed inputs"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_offline_verification_binds_replayed_project_inventory(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["projects"][0]["active"] = False
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="project inventory"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_offline_verification_binds_profile_consumed_inputs(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["closures"]["consumed_inputs"] = []
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="consumed inputs"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_offline_verification_binds_profile_license_evidence(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    consumed = lock["closures"]["consumed_inputs"][0]
    consumed["license_expression"] = "Invented-1.0"
    consumed["license_evidence"] = ["invented:evidence"]
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="consumed inputs"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_semantics_reject_noncanonical_import_order(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["manifest"]["imports"].reverse()
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="canonical order"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_semantics_reject_fail_open_consumed_input(offline_fixture):
    original, cache = offline_fixture
    lock = copy.deepcopy(original)
    lock["closures"]["consumed_inputs"][0]["disposition"]["ai_input"] = "deny"
    lock["lock_id"] = source_lock_id(lock)
    with pytest.raises(SourceLockError, match="fail closed"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=True)


def test_semantics_allow_explicit_unavailable_inactive_project(offline_fixture):
    original, _ = offline_fixture
    lock = copy.deepcopy(original)
    lock["projects"].append(
        {
            "active": False,
            "groups": ["private"],
            "manifest_revision": "main",
            "name": "private-project",
            "optional": False,
            "path": "private-project",
            "resolved_commit": None,
            "resolution_status": "unavailable-inactive",
            "selected": False,
            "url": "https://example.invalid/private.git",
        }
    )
    _validate_lock_semantics(lock)


def test_canonical_identity_is_order_independent_for_object_keys(offline_fixture):
    lock, _ = offline_fixture
    reversed_lock = dict(reversed(list(lock.items())))
    assert canonical_json_bytes(lock) == canonical_json_bytes(reversed_lock)
    assert source_lock_id(lock) == source_lock_id(reversed_lock)


def test_resolve_rejects_nonexact_revisions_before_network(tmp_path):
    with pytest.raises(SourceLockError, match="manifest revision"):
        resolve_source_lock(
            manifest_url="https://example.invalid/manifest.git",
            manifest_revision="main",
            board="fixture",
            device="MCXFIXTURE",
            profile_spec=tmp_path / "missing.json",
            cache_dir=tmp_path / "cache",
            output=tmp_path / "lock.json",
            resolver_revision="1" * 40,
        )


def test_verify_requires_explicit_offline_mode(offline_fixture):
    lock, cache = offline_fixture
    with pytest.raises(SourceLockError, match="explicitly offline"):
        verify_source_lock(lock=lock, cache_dir=cache, offline=False)
