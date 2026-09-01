"""``nxp-monkey kex-lock`` -- create and verify KEX acquisition locks."""
from __future__ import annotations

import argparse
import json

from rich_argparse import RichHelpFormatter

from .kex_lock import create_kex_acquisition_lock, verify_kex_acquisition_lock


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``kex-lock`` command group."""
    parser = subparsers.add_parser(
        "kex-lock",
        help="Create and verify deterministic KEX acquisition locks",
        description="Bind exact retained KEX archives, files, schemas, and JSON mirrors.",
        formatter_class=RichHelpFormatter,
    )
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    create = actions.add_parser("create", help="Create a KEX acquisition lock")
    create.add_argument("--part", required=True)
    create.add_argument("--upstream-version", required=True)
    create.add_argument("--variant", action="append", required=True)
    create.add_argument("--producer-revision", required=True)
    create.add_argument("--cache", required=True)
    create.add_argument("--output", required=True)
    create.set_defaults(func=run_create)
    verify = actions.add_parser("verify", help="Verify retained KEX bytes offline")
    verify.add_argument("--lock", required=True)
    verify.add_argument("--cache", required=True)
    verify.add_argument("--offline", action="store_true", required=True)
    verify.set_defaults(func=run_verify)


def run_create(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey kex-lock create``."""
    lock = create_kex_acquisition_lock(
        part=args.part,
        upstream_version=args.upstream_version,
        variants=args.variant,
        cache_dir=args.cache,
        output=args.output,
        producer_revision=args.producer_revision,
    )
    print(args.output if args.json else lock["lock_id"])
    return 0


def run_verify(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey kex-lock verify``."""
    result = verify_kex_acquisition_lock(lock=args.lock, cache_dir=args.cache, offline=args.offline)
    print(json.dumps(result, sort_keys=True) if args.json else f"verified {result['lock_id']}")
    return 0
