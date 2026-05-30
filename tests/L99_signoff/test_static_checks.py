"""L99: run the same static checks CI runs (ADR-0005).

Keeps the local ``pytest`` run in lockstep with ``ci.yml``: if pyright
or ruff regresses, the L99 gate trips locally before the change ever
lands on CI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_ruff_check_clean():
    """``ruff check src tests`` reports no errors."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_pyright_clean():
    """``pyright`` reports no errors across the project."""
    result = subprocess.run(
        [sys.executable, "-m", "pyright"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"pyright failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
