"""``nxp-monkey cache`` — inspect and manage the on-disk cache.

Thin wrapper around :func:`nxp_monkey.cache_path`,
:func:`nxp_monkey.cache_size`, and :func:`nxp_monkey.cache_clear`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .cache import cache_clear, cache_path, cache_size
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``cache`` subcommand group on ``subparsers``."""
    parser = subparsers.add_parser(
        "cache",
        help="Inspect and manage the on-disk cache",
        description=(
            "Inspect or modify the platformdirs-managed nxp_monkey cache. "
            "See ADR-0006 for the cache layout contract."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey cache path\n"
            "  nxp-monkey cache size\n"
            "  nxp-monkey cache clear                       # everything\n"
            "  nxp-monkey cache clear --scope versions      # versions + family list\n"
            "  nxp-monkey cache clear --scope index         # search index only\n"
            "  nxp-monkey cache clear --scope 25.12.10      # one tool version's data\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    actions = parser.add_subparsers(
        dest="action",
        metavar="ACTION",
        required=True,
    )

    p_path = actions.add_parser(
        "path",
        help="Print the cache root directory",
        formatter_class=RichHelpFormatter,
    )
    p_path.set_defaults(func=run_path)

    p_size = actions.add_parser(
        "size",
        help="Print the total cache size in bytes and human-readable form",
        formatter_class=RichHelpFormatter,
    )
    p_size.set_defaults(func=run_size)

    p_clear = actions.add_parser(
        "clear",
        help="Remove cached content",
        formatter_class=RichHelpFormatter,
    )
    p_clear.add_argument(
        "--scope",
        metavar="SCOPE",
        default="all",
        help=(
            "What to remove: 'all', 'versions' (KEX versions.json + the "
            "portfolio/family map portfolio.json), 'index' (the SQLite "
            "search index), or an explicit tool version like '25.12.10' "
            "(removes that version's processors tree). Default: all."
        ),
    )
    p_clear.set_defaults(func=run_clear)


def run_path(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey cache path``."""
    root = cache_path()
    if args.json:
        print(write_json(Path.cwd() / "cache-path.json", {"path": root}))
        return 0
    print(root)
    return 0


def run_size(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey cache size``."""
    bytes_ = cache_size()
    if args.json:
        payload = {"bytes": bytes_, "human": _human_bytes(bytes_)}
        print(write_json(Path.cwd() / "cache-size.json", payload))
        return 0
    print(f"{bytes_}\t{_human_bytes(bytes_)}")
    return 0


def run_clear(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey cache clear``."""
    cache_clear(args.scope)
    if args.json:
        payload = {"cleared_scope": args.scope}
        print(write_json(Path.cwd() / "cache-clear.json", payload))
        return 0
    print(f"cleared scope: {args.scope}")
    return 0


def _human_bytes(n: int) -> str:
    """Format a byte count as a short human-readable string."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"
