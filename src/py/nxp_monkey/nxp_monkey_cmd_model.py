"""``nxp-monkey model`` -- normalize and compare portable source models."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .model import compare_models, normalize_model


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``model`` command group."""
    parser = subparsers.add_parser(
        "model",
        help="Normalize and compare portable source models",
        description="Build canonical model-v1 JSON from a verified offline source lock.",
        formatter_class=RichHelpFormatter,
    )
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    normalize = actions.add_parser("normalize", help="Normalize a verified source lock")
    normalize.add_argument("--lock", required=True)
    normalize.add_argument("--cache", required=True)
    normalize.add_argument("--offline", action="store_true", required=True)
    normalize.add_argument("--output", required=True)
    normalize.set_defaults(func=run_normalize)

    compare = actions.add_parser("compare", help="Compare model or adapter JSON")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--output", required=True)
    compare.set_defaults(func=run_compare)


def run_normalize(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey model normalize``."""
    model = normalize_model(
        lock=args.lock, cache_dir=args.cache, output=args.output, offline=args.offline
    )
    print(str(Path(args.output).resolve()) if args.json else model["model_id"])
    return 0


def run_compare(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey model compare``."""
    report = compare_models(left=args.left, right=args.right, output=args.output)
    print(
        str(Path(args.output).resolve())
        if args.json
        else f"{report['summary']['total_differences']} differences"
    )
    return 2 if report["summary"]["unclassified"] else 0
