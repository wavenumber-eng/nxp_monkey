"""``nxp-monkey search`` — search indexed or portfolio parts.

Thin wrapper around :func:`nxp_monkey.search`.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .search import DEFAULT_LIMIT, search
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``search`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "search",
        help="Search parts by name or family root",
        description=(
            "Search the local nxp_monkey index, falling back to the NXP "
            "portfolio map when the index has not been built. Partial "
            "matches are supported by default; pass --fuzzy to also rank "
            "by edit-ratio similarity."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey search MCXA\n"
            "  nxp-monkey search MCXA156\n"
            "  nxp-monkey search mcxa --fuzzy\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "query",
        metavar="QUERY",
        help="Part / family search string (case-insensitive)",
    )
    behavior = parser.add_argument_group("behavior")
    behavior.add_argument(
        "--fuzzy",
        action="store_true",
        help="Also rank results by edit-ratio similarity",
    )
    behavior.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of hits to return (default: {DEFAULT_LIMIT})",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ``search`` subcommand."""
    hits = search(args.query, fuzzy=args.fuzzy, limit=args.limit)
    if args.json:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", args.query.strip()) or "query"
        payload = {
            "query": args.query,
            "fuzzy": args.fuzzy,
            "limit": args.limit,
            "hits": hits,
        }
        print(write_json(Path.cwd() / f"search-{slug}.json", payload))
        return 0 if hits else 1

    if not hits:
        print("(no matches)")
        return 1
    print("part\tfamily\tscore\tmatched")
    for hit in hits:
        print(f"{hit.part}\t{hit.family}\t{hit.score:.2f}\t{hit.matched_field}")
    return 0
