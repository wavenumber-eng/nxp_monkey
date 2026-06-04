"""L99: release metadata sanity checks (ADR-0001)."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import nxp_monkey

REPO = Path(__file__).resolve().parents[2]


def test_version_is_pep440_date_form():
    """The package version is ``YYYY.M.D`` or ``YYYY.M.D.N`` per ADR-0001."""
    pattern = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")
    assert pattern.match(nxp_monkey.__version__), (
        f"version {nxp_monkey.__version__!r} does not match the date-based PEP 440 form"
    )


def test_changelog_mentions_current_version():
    """``CHANGELOG.md`` contains an entry for the current package version."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert nxp_monkey.__version__ in text, "CHANGELOG.md is missing the current version"


def test_release_note_mentions_current_version():
    """The dated release note exists and mentions the current package version."""
    year, month, day, *_ = (int(part) for part in nxp_monkey.__version__.split("."))
    release_date = date(year, month, day)
    note_path = REPO / "docs" / "releases" / f"{release_date:%Y-%m-%d}.md"

    assert note_path.exists(), f"missing release note: {note_path}"
    text = note_path.read_text(encoding="utf-8")
    assert f"`{nxp_monkey.__version__}`" in text


def test_pyproject_version_matches_version_module():
    """The version in pyproject.toml matches ``nxp_monkey.__version__``."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version line"
    assert match.group(1) == nxp_monkey.__version__
