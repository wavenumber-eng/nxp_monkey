"""``nxp-monkey roadmap`` — print the per-part inferred-schema + agent guide.

Thin wrapper around :func:`nxp_monkey.build_roadmap`. Fetches the part on
demand when the cache is empty for the requested variant, then walks the
unpacked tree and emits a roadmap dict pointing agents at where things
live (key files, optional sections, package variants, codegen entry
points, XML namespaces).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rich_argparse import RichHelpFormatter

from .details import details
from .fetch import DEFAULT_SDK_VARIANTS, DEFAULT_VARIANT
from .roadmap import build_roadmap
from .serialization import write_json


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``roadmap`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "roadmap",
        help="Print a per-part inferred-schema + agent guide for one tree",
        description=(
            "Walk one part's unpacked data tree and emit a roadmap "
            "describing where things live: key files, optional sections "
            "(security/dcdx/ddr/mem_validation), per-package variants, "
            "codegen script categories (including Zephyr DT entry "
            "points), and the distinct XML namespaces observed. Fetches "
            "the part on demand when the cache is empty. There are no "
            "public XSDs for KEX XML — the roadmap's xml_namespaces "
            "list is identifier-only."
        ),
        epilog=(
            "Examples:\n"
            "  nxp-monkey roadmap MCXA156\n"
            "  nxp-monkey roadmap MCXA156 --variant zephyr3_2\n"
            "  nxp-monkey --json roadmap MCXA156\n"
        ),
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument(
        "part",
        metavar="PART",
        help="Processor id (for example MCXA156)",
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
    """Execute the ``roadmap`` subcommand."""
    info = details(args.part, variant=args.variant, version=args.version)
    roadmap = build_roadmap(info.root)
    if args.json:
        out = Path.cwd() / f"{info.part}.roadmap.json"
        print(write_json(out, roadmap))
        return 0
    print(f"part:    {roadmap['part']}")
    print(f"variant: {roadmap['variant']}")
    print(f"root:    {roadmap['root']}")
    print("guide:")
    for step in roadmap["guide"]:
        print(f"  - {step}")
    print("key_files:")
    for name, rel in roadmap["key_files"].items():
        print(f"  {name:<22} {rel}")
    on = [k for k, present in roadmap["optional_sections"].items() if present]
    print(f"optional_sections: {', '.join(on) if on else '(none)'}")
    if roadmap["package_variants"]:
        print(f"package_variants ({len(roadmap['package_variants'])}):")
        for v in roadmap["package_variants"]:
            print(f"  - {v['variant']:<24} manifest={v['manifest']}")
    codegen = roadmap["codegen"]
    if codegen.get("scripts_root"):
        cats = ", ".join(codegen.get("categories", [])) or "(none)"
        print(f"codegen.scripts_root: {codegen['scripts_root']}")
        print(f"codegen.categories:   {cats}")
        if codegen.get("zephyr_dt"):
            print("codegen.zephyr_dt:")
            for label, rel in codegen["zephyr_dt"].items():
                print(f"  {label:<10} {rel}")
    if roadmap["xml_namespaces"]:
        print(f"xml_namespaces ({len(roadmap['xml_namespaces'])}):")
        for ns in roadmap["xml_namespaces"]:
            prefix = ns.get("prefix") or "(default)"
            label = ns.get("label", "")
            version = ns.get("version", "")
            tag = " ".join(t for t in (label, version) if t)
            tag = f"  [{tag}]" if tag else ""
            print(f"  {prefix:<14} {ns['uri']}{tag}")
    return 0
