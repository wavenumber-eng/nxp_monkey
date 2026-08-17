"""``nxp-monkey source`` -- resolve and verify official Git source locks."""
from __future__ import annotations

import argparse
import json

from rich_argparse import RichHelpFormatter

from .source_lock import resolve_source_lock, verify_source_lock


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``source`` command group."""
    parser = subparsers.add_parser(
        "source",
        help="Resolve and verify official MCUXpresso source locks",
        description=(
            "Resolve exact official MCUXpresso Git inputs or verify a retained "
            "source lock without network access. Source-lock v0 excludes KEX."
        ),
        formatter_class=RichHelpFormatter,
    )
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)

    resolve = actions.add_parser(
        "resolve", help="Resolve an exact manifest revision into a source lock"
    )
    resolve.add_argument("--manifest-url", required=True)
    resolve.add_argument("--manifest-revision", required=True)
    resolve.add_argument("--board", required=True)
    resolve.add_argument("--device", required=True)
    resolve.add_argument("--profile-spec", required=True)
    resolve.add_argument("--resolver-revision", required=True)
    resolve.add_argument("--kex-policy", choices=("not-used",), required=True)
    resolve.add_argument("--cache", required=True)
    resolve.add_argument("--output", required=True)
    resolve.set_defaults(func=run_resolve)

    verify = actions.add_parser(
        "verify", help="Verify a retained lock and cache with network disabled"
    )
    verify.add_argument("--lock", required=True)
    verify.add_argument("--cache", required=True)
    verify.add_argument("--offline", action="store_true", required=True)
    verify.set_defaults(func=run_verify)


def run_resolve(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey source resolve``."""
    lock = resolve_source_lock(
        manifest_url=args.manifest_url,
        manifest_revision=args.manifest_revision,
        board=args.board,
        device=args.device,
        profile_spec=args.profile_spec,
        cache_dir=args.cache,
        output=args.output,
        resolver_revision=args.resolver_revision,
        kex_policy=args.kex_policy,
    )
    if args.json:
        print(args.output)
    else:
        print(lock["lock_id"])
    return 0


def run_verify(args: argparse.Namespace) -> int:
    """Execute ``nxp-monkey source verify``."""
    result = verify_source_lock(lock=args.lock, cache_dir=args.cache, offline=args.offline)
    print(json.dumps(result, sort_keys=True) if args.json else f"verified {result['lock_id']}")
    return 0
