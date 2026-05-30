"""``nxp-monkey index`` — build and inspect the local search index.

Thin wrapper around :func:`nxp_monkey.build_index`, :func:`nxp_monkey.get_part`,
and :func:`nxp_monkey.index_meta`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .index import build_index, get_part, index_meta
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``index`` subcommand group on ``subparsers``."""
    parser = subparsers.add_parser(
        "index",
        help="Build and inspect the local search index",
        description=(
            "Manage the local SQLite index that backs `nxp-monkey search`. "
            "Sub-actions: build (populate from upstream), show (display "
            "what's known about one part), info (print build metadata)."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey index build\n"
            "  nxp-monkey index build --probe-variants\n"
            "  nxp-monkey index show MCXA156\n"
            "  nxp-monkey index info\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    actions = parser.add_subparsers(
        dest="action",
        metavar="ACTION",
        required=True,
    )

    p_build = actions.add_parser(
        "build",
        help="Build or refresh the local index from the KEX upstream",
        formatter_class=RichHelpFormatter,
    )
    p_build.add_argument(
        "--variant",
        metavar="VARIANT",
        default=None,
        help="Restrict recorded variants to a single name",
    )
    p_build.add_argument(
        "--probe-variants",
        action="store_true",
        help="Hit upstream to discover real variants per family (slower)",
    )
    p_build.set_defaults(func=run_build)

    p_show = actions.add_parser(
        "show",
        help="Display the indexed record for one part",
        formatter_class=RichHelpFormatter,
    )
    p_show.add_argument("part", metavar="PART")
    p_show.set_defaults(func=run_show)

    p_info = actions.add_parser(
        "info",
        help="Print metadata about the current index build",
        formatter_class=RichHelpFormatter,
    )
    p_info.set_defaults(func=run_info)


def run_build(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey index build``."""
    meta = build_index(variant=args.variant, probe_variants=args.probe_variants)
    if args.json:
        print(write_json(Path.cwd() / "index-build.json", meta))
        return 0
    print(
        f"built at {meta.built_at} | source={meta.source_version} | "
        f"parts={meta.part_count} | families={meta.family_count}"
    )
    return 0


def run_show(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey index show <part>``."""
    info = get_part(args.part)
    if info is None:
        if args.json:
            out = Path.cwd() / f"{args.part}.index.json"
            print(write_json(out, {"part": args.part, "found": False}))
            return 1
        print(f"(no index entry for {args.part!r}; try `nxp-monkey index build`)")
        return 1
    if args.json:
        print(write_json(Path.cwd() / f"{info.part}.index.json", info))
        return 0
    print(f"part:    {info.part}")
    print(f"family:  {info.family}")
    print(f"version: {info.version}")
    print(f"variants: {', '.join(info.variants) if info.variants else '(none)'}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey index info``."""
    meta = index_meta()
    if meta is None:
        if args.json:
            print(write_json(Path.cwd() / "index-info.json", {"built": False}))
            return 1
        print("(no index built; run `nxp-monkey index build`)")
        return 1
    if args.json:
        print(write_json(Path.cwd() / "index-info.json", meta))
        return 0
    print(f"built_at:       {meta.built_at}")
    print(f"source_version: {meta.source_version}")
    print(f"part_count:     {meta.part_count}")
    print(f"family_count:   {meta.family_count}")
    return 0
