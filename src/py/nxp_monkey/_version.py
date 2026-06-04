"""Single source of truth for the package version.

Hatchling reads the version from this file at build time (see pyproject.toml
``[tool.hatch.version]``). The release workflow verifies that this value
matches the Git tag (see ADR-0001).
"""
from __future__ import annotations

__version__ = "2026.6.4"
