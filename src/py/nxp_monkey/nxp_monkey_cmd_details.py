"""``nxp-monkey details`` — show the structured spine of one part's data tree.

Thin wrapper around :func:`nxp_monkey.details`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .details import details
from .fetch import DEFAULT_SDK_VARIANTS, DEFAULT_VARIANT
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``details`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "details",
        help="Show structured details for one part (header, cores, variants)",
        description=(
            "Parse the small spine XML files of one part's KEX data tree "
            "and print the result. Fetches the part on demand if the local "
            "cache is empty. Heavier payloads (registers, signal "
            "configuration, clocks) are deliberately not eagerly parsed; "
            "see docs/design/api/details.html for the per-variant db_links."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey details MCXA132\n"
            "  nxp-monkey details MCXA132 --variant zephyr3_2\n"
            "  nxp-monkey details MCXA132 --version 25.12.10\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "part",
        metavar="PART",
        help="Processor id (for example MCXA132)",
    )
    parser.add_argument(
        "--variant",
        metavar="VARIANT",
        default=DEFAULT_VARIANT,
        choices=list(DEFAULT_SDK_VARIANTS),
        help=f"SDK variant to load (default: {DEFAULT_VARIANT})",
    )
    parser.add_argument(
        "--version",
        metavar="VERSION",
        default=None,
        help="Explicit tool version (default: portfolio-latest per family)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the ``details`` subcommand."""
    info = details(args.part, variant=args.variant, version=args.version)
    if args.json:
        out = Path.cwd() / f"{info.part}.details.json"
        print(write_json(out, info))
        return 0
    print(f"part:             {info.part}")
    print(f"variant_id:       {info.variant_id}")
    print(f"version:          {info.version}")
    print(f"family:           {info.header.family}")
    print(f"series:           {info.header.series}")
    print(f"producer:         {info.header.producer}")
    print(f"default_part:     {info.header.default_part}")
    print(f"enabled_tools:    {', '.join(info.header.enabled_tools)}")
    print(f"target_products:  {', '.join(info.header.target_products)}")
    print(f"is_app_processor: {info.is_application_processor}")
    print(f"root:             {info.root}")
    if info.properties:
        print("properties:")
        for key, value in sorted(info.properties.items()):
            print(f"  {key} = {value}")
    if info.cores:
        print("cores:")
        for core in info.cores:
            print(f"  - {core.name}  id={core.core_id}  {core.description}")
    if info.variants:
        print(f"variants ({len(info.variants)}):")
        for variant in info.variants:
            print(
                f"  - {variant.variant}  package={variant.package or '(none)'}  "
                f"db_links={len(variant.db_links)}"
            )
    return 0
