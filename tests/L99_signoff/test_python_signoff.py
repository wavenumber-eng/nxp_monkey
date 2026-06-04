"""L99: Python hygiene signoff."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_python_hygiene_signoff_passes():
    """AST-based complexity, annotation, and Any ratchet checks pass."""
    result = subprocess.run(
        [sys.executable, "tests/support_scripts/py_signoff.py"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
