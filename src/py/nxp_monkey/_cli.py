"""CLI orchestrator.

This module owns the root :class:`argparse.ArgumentParser`, global options,
subcommand registration, dispatch, and the ``nxp-monkey help <cmd>`` alias.
It must contain no business logic. Every public subcommand lives in its own
``nxp_monkey_cmd_<name>.py`` module and is responsible for its own argparse
configuration; see ADR-0002 and ADR-0009.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from rich_argparse import RichHelpFormatter

from ._version import __version__
from .kex_client import NxpFetchError

# Color palette tuned to high-contrast monochrome with one accent.
# See ADR-0009 for the rationale.
RichHelpFormatter.styles = {
    "argparse.args": "bold cyan",
    "argparse.groups": "bold white",
    "argparse.help": "default",
    "argparse.metavar": "yellow",
    "argparse.prog": "bold magenta",
    "argparse.syntax": "bold",
    "argparse.text": "default",
    "argparse.default": "italic dim",
}


CommandFn = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    """Construct the root parser with every public subcommand registered.

    Returns:
        Root :class:`argparse.ArgumentParser` ready to parse ``sys.argv``.
    """
    parser = argparse.ArgumentParser(
        prog="nxp-monkey",
        description=(
            "Cross-platform CLI for fetching, indexing, and searching NXP "
            "MCUXpresso Config Tools chip data. nxp-monkey is also a Python "
            "library; see `docs/design/api/` for the public import surface."
        ),
        epilog=(
            "Full design docs: docs/design/cli/<command>.html\n"
            "ADRs:             docs/adrs/\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nxp-monkey {__version__}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Switch stdout from human-readable output to the absolute "
            "path of the JSON file produced for this invocation. Most "
            "commands write a JSON file to the working directory under "
            "this flag. `fetch` always writes per-part JSON sidecars + "
            "XML->JSON mirror regardless of this flag; --json on fetch "
            "additionally writes a top-level fetch.json invocation "
            "summary. Use this for agent / tool integration."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="COMMAND",
    )

    from . import (
        nxp_monkey_cmd_cache as cmd_cache,
    )
    from . import (
        nxp_monkey_cmd_details as cmd_details,
    )
    from . import (
        nxp_monkey_cmd_families as cmd_families,
    )
    from . import (
        nxp_monkey_cmd_fetch as cmd_fetch,
    )
    from . import (
        nxp_monkey_cmd_index as cmd_index,
    )
    from . import (
        nxp_monkey_cmd_roadmap as cmd_roadmap,
    )
    from . import (
        nxp_monkey_cmd_search as cmd_search,
    )
    from . import (
        nxp_monkey_cmd_versions as cmd_versions,
    )

    for module in (
        cmd_versions,
        cmd_families,
        cmd_search,
        cmd_index,
        cmd_fetch,
        cmd_details,
        cmd_roadmap,
        cmd_cache,
    ):
        module.register(subparsers)

    # `nxp-monkey help <cmd>` -> `nxp-monkey <cmd> --help`
    help_parser = subparsers.add_parser(
        "help",
        help="Show help for another command",
        description="Show --help for another command.",
        formatter_class=RichHelpFormatter,
    )
    help_parser.add_argument("topic", nargs="?", metavar="COMMAND")
    help_parser.set_defaults(func=_run_help, _parser_ref=parser)

    return parser


def _run_help(args: argparse.Namespace) -> int:
    """Dispatch ``nxp-monkey help <cmd>`` to ``<cmd> --help``."""
    parser: argparse.ArgumentParser = args._parser_ref
    if not args.topic:
        parser.print_help()
        return 0
    # Re-invoke with --help on the named subcommand.
    parser.parse_args([args.topic, "--help"])
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point used by ``console_scripts`` and ``python -m nxp_monkey``.

    Args:
        argv: Optional explicit argument list. ``None`` falls back to
            ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    func: CommandFn | None = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1

    try:
        return int(func(args) or 0)
    except NxpFetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
