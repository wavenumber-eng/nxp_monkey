"""``nxp-monkey versions`` — list KEX tool versions.

Thin wrapper around :func:`nxp_monkey.list_versions`. See
``docs/design/cli/versions.html`` for the design doc and
``docs/contracts/command_manifest.v0.json`` for the manifest entry.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .kex_client import KexClient, latest_version
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``versions`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "versions",
        help="List MCUXpresso tool versions published by NXP",
        description=(
            "List MCUXpresso Config Tools API versions known to NXP's KEX "
            "endpoint. Results are cached locally for 24 hours; pass "
            "--refresh to bypass the cache."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey versions\n"
            "  nxp-monkey versions --latest\n"
            "  nxp-monkey versions --include-unpublished\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    output = parser.add_argument_group("output")
    output.add_argument(
        "--latest",
        action="store_true",
        help="Print just the highest published version and exit",
    )
    output.add_argument(
        "--include-unpublished",
        action="store_true",
        help="Include rows with an empty 'version' field",
    )
    output.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the local 24h version cache and re-fetch",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ``versions`` subcommand."""
    if args.latest:
        latest = latest_version()
        if args.json:
            print(write_json(Path.cwd() / "versions-latest.json", {"latest": latest}))
        else:
            print(latest)
        return 0

    client = KexClient(include_unpublished=args.include_unpublished)
    versions = client.list_versions(refresh=args.refresh)
    if not args.include_unpublished:
        versions = [item for item in versions if item.stable]

    if args.json:
        payload = {"versions": versions}
        print(write_json(Path.cwd() / "versions.json", payload))
        return 0

    print("name\tapi_id\tversion\tstable")
    for item in versions:
        print(f"{item.name}\t{item.api_id}\t{item.version}\t{str(item.stable).lower()}")
    return 0
