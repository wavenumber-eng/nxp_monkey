"""Smoke tests that invoke the CLI as a subprocess.

These tests do not hit the network. They verify that:

- ``python -m nxp_monkey --version`` prints the package version;
- ``python -m nxp_monkey version`` prints package and dependency versions;
- every public subcommand accepts ``--help`` and exits 0;
- the root parser dispatches the ``help <cmd>`` alias.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

PUBLIC_COMMANDS = (
    "version",
    "versions",
    "families",
    "search",
    "index",
    "fetch",
    "details",
    "cache",
    "help",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nxp_monkey", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_version_prints_pep440():
    """``--version`` prints a PEP 440 version string."""
    from nxp_monkey._version import __version__

    proc = _run("--version")
    assert proc.returncode == 0, proc.stderr
    assert __version__ in proc.stdout


def test_version_command_prints_runtime_versions():
    """``version`` prints package, Python, and runtime dependency versions."""
    from nxp_monkey._version import __version__

    proc = _run("version")
    assert proc.returncode == 0, proc.stderr
    assert f"nxp-monkey {__version__}" in proc.stdout
    assert "python " in proc.stdout
    assert "rich " in proc.stdout


def test_version_command_prints_json():
    """``version --format json`` prints a machine-readable version record."""
    from nxp_monkey._version import __version__

    proc = _run("version", "--format", "json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["nxp-monkey"] == __version__
    assert "python" in payload
    assert "rich" in payload


def test_root_help_exits_clean():
    """Root ``--help`` exits 0 and mentions a known subcommand."""
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "versions" in proc.stdout


@pytest.mark.parametrize("cmd", PUBLIC_COMMANDS)
def test_subcommand_help_exits_clean(cmd: str):
    """Every public subcommand prints ``--help`` and exits 0."""
    proc = _run(cmd, "--help")
    assert proc.returncode == 0, f"{cmd}: {proc.stderr}"


def test_help_alias_dispatches():
    """``nxp-monkey help versions`` is equivalent to ``nxp-monkey versions --help``."""
    proc = _run("help", "versions")
    assert proc.returncode == 0, proc.stderr
    assert "MCUXpresso" in proc.stdout or "versions" in proc.stdout
