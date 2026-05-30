"""``nxp-monkey families`` — list processor families.

Thin wrapper around :func:`nxp_monkey.list_families` and
:func:`nxp_monkey.portfolio_latest_map`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .kex_client import KexClient
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``families`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "families",
        help="List downloadable processor families",
        description=(
            "List the processor families available from NXP's KEX storage "
            "tree. By default, shows the newest tool version that "
            "publishes each family (the portfolio-latest view that "
            "matches the merged listing used by the MCUXpresso Data "
            "Manager). Pass --version to list a specific tool version "
            "instead."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey families\n"
            "  nxp-monkey families --portfolio-latest\n"
            "  nxp-monkey families --version 25.12.10\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    filters = parser.add_argument_group("filters")
    filters.add_argument(
        "--version",
        metavar="VERSION",
        default=None,
        help="Tool version to list (default: portfolio-latest)",
    )
    filters.add_argument(
        "--portfolio-latest",
        action="store_true",
        help="List newest tool version per family (default behavior when --version is not given)",
    )
    output = parser.add_argument_group("output")
    output.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=None,
        help="Limit number of families shown",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ``families`` subcommand."""
    client = KexClient()
    if args.version is None or args.portfolio_latest:
        portfolio = client.portfolio_latest_map()
        items = list(portfolio.items())
        if args.limit is not None:
            items = items[: args.limit]
        if args.json:
            payload = {
                "mode": "portfolio_latest",
                "families": [{"name": f, "version": v} for f, v in items],
            }
            print(write_json(Path.cwd() / "families.json", payload))
            return 0
        for family, version in items:
            print(f"{family}\t{version}")
        return 0

    families = client.list_processor_families(version=args.version)
    if args.limit is not None:
        families = families[: args.limit]
    if args.json:
        payload = {
            "mode": "version",
            "version": args.version,
            "families": [{"name": e.name} for e in families],
        }
        print(write_json(Path.cwd() / "families.json", payload))
        return 0
    for entry in families:
        print(entry.name)
    return 0
