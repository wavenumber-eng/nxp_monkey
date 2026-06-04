"""``nxp-monkey version`` - print package and runtime dependency versions."""
from __future__ import annotations

import argparse
import json
import platform
from importlib import metadata

from rich_argparse import RichHelpFormatter

from ._version import __version__

_DEPENDENCIES = ("platformdirs", "rich", "rich-argparse")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``version`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "version",
        help="Print package and runtime dependency versions",
        description="Print nxp-monkey, Python, and major runtime dependency versions.",
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. Default: text.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ``version`` subcommand."""
    versions = _version_payload()
    if args.format == "json":
        print(json.dumps(versions, indent=2, sort_keys=True))
        return 0

    for name, value in versions.items():
        print(f"{name} {value}")
    return 0


def _version_payload() -> dict[str, str]:
    """Return package, Python, and major runtime dependency versions."""
    payload = {
        "nxp-monkey": __version__,
        "python": f"{platform.python_implementation()} {platform.python_version()}",
    }
    for dependency in _DEPENDENCIES:
        payload[dependency] = metadata.version(dependency)
    return payload
