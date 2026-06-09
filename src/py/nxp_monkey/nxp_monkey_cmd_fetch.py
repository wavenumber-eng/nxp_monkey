"""``nxp-monkey fetch`` — fetch part data trees into the local cache.

Thin wrapper around :func:`nxp_monkey.fetch` and
:func:`nxp_monkey.fetch_all`. By default, fetches every SDK variant
NXP publishes for the part (ksdk2_0 + zephyr3_2 + i_mx_2_0); variants
that the part does not publish are silently skipped.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich_argparse import RichHelpFormatter

from ._matching import portfolio_part_matches
from .details import details_from_cache
from .fetch import (
    DEFAULT_SDK_VARIANTS,
    DEFAULT_VARIANT,
    fetch,
    fetch_all,
    mirror_part_variant,
)
from .kex_client import KexClient, NxpFetchError
from .roadmap import build_roadmap
from .serialization import write_json
from .xml_json import mirror_xml_tree_as_json

FETCH_DESCRIPTION = (
    "Download and unpack NXP MCUXpresso Config Tools data for one "
    "or more parts into the local cache. Positional PART values may "
    "be exact portfolio keys, concrete orderable numbers that match "
    "NXP masked keys, or prefixes such as MIMX93. By default every SDK "
    "variant NXP publishes for the part is fetched in parallel "
    "(ksdk2_0 + zephyr3_2 + i_mx_2_0); pass --variant to restrict "
    "to one. Unpublished variants are silently skipped."
)

FETCH_EPILOG = (
    "Examples:\n"
    "  nxp-monkey fetch MCXA156\n"
    "  nxp-monkey fetch MCXA156 --variant ksdk2_0\n"
    "  nxp-monkey fetch MIMX93 --variant ksdk2_0\n"
    "  nxp-monkey fetch MCXA156 --output ./board_prep\n"
    "  nxp-monkey fetch MCXA156 MCXA266\n"
    "  nxp-monkey fetch --family MCXA\n"
    "  nxp-monkey fetch --all\n"
    "\n"
    "Every fetch writes a per-part folder split by media type:\n"
    "  <output>/<PART>/xml/<VARIANT>/...     # raw NXP binders\n"
    "  <output>/<PART>/json/<PART>.json      # PartDetails spine\n"
    "  <output>/<PART>/json/<PART>.roadmap.json  # inferred schema + guide\n"
    "  <output>/<PART>/json/<VARIANT>/...    # full XML->JSON mirror\n"
    "                                        # (one .json per .xml)\n"
    "Use --no-json-mirror to skip the parallel tree, or\n"
    "--json-mirror-only / --json-mirror-skip to filter by glob.\n"
    "--json additionally writes a top-level <cwd>/fetch.json\n"
    "invocation summary and prints its path on stdout.\n"
)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``fetch`` subcommand on ``subparsers``."""
    parser = subparsers.add_parser(
        "fetch",
        help="Fetch one or more parts' data trees into the local cache",
        description=FETCH_DESCRIPTION,
        epilog=FETCH_EPILOG,
        formatter_class=RichHelpFormatter,
    )
    _add_selection_args(parser)
    _add_behavior_args(parser)
    _add_json_mirror_args(parser)
    parser.set_defaults(func=run)


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    """Add fetch selection arguments to ``parser``."""
    selection = parser.add_argument_group("selection")
    selection.add_argument(
        "parts",
        nargs="*",
        metavar="PART",
        help=(
            "One or more exact parts, concrete orderable aliases, or prefixes "
            "to fetch (omit when using --family or --all)"
        ),
    )
    selection.add_argument(
        "--family",
        metavar="PREFIX",
        default=None,
        help="Fetch every part whose name starts with PREFIX",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Fetch every part in the portfolio",
    )


def _add_behavior_args(parser: argparse.ArgumentParser) -> None:
    """Add fetch behavior arguments to ``parser``."""
    behavior = parser.add_argument_group("behavior")
    behavior.add_argument(
        "--variant",
        metavar="VARIANT",
        default=None,
        choices=list(DEFAULT_SDK_VARIANTS),
        help=(
            "Restrict to one SDK variant "
            f"(default: fetch all of {', '.join(DEFAULT_SDK_VARIANTS)})"
        ),
    )
    behavior.add_argument(
        "--version",
        metavar="VERSION",
        default=None,
        help="Explicit tool version (default: portfolio-latest per family)",
    )
    behavior.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even when the cached tree already exists",
    )
    behavior.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        type=Path,
        help=(
            "After populating the cache, mirror each unpacked tree to "
            "DIR/<PART>/xml/<VARIANT>/ and write JSON sidecars "
            "(PartDetails spine, roadmap, full XML->JSON mirror) under "
            "DIR/<PART>/json/. DIR is created if it does not exist. "
            "Cache is always populated. Default: current working "
            "directory."
        ),
    )


def _add_json_mirror_args(parser: argparse.ArgumentParser) -> None:
    """Add XML-to-JSON mirror arguments to ``parser``."""
    json_group = parser.add_argument_group("json mirror")
    json_group.add_argument(
        "--no-json-mirror",
        action="store_true",
        help=(
            "Skip the full XML->JSON mirror tree. Only the spine files "
            "(<PART>.json + <PART>.roadmap.json) are written under json/."
        ),
    )
    json_group.add_argument(
        "--json-mirror-only",
        metavar="PATTERN",
        action="append",
        default=None,
        help=(
            "Restrict the XML->JSON mirror to paths matching this glob, "
            "evaluated against the variant-relative path "
            "(e.g. '**/registers/**'). Repeatable."
        ),
    )
    json_group.add_argument(
        "--json-mirror-skip",
        metavar="PATTERN",
        action="append",
        default=None,
        help=(
            "Skip paths matching this glob from the XML->JSON mirror "
            "(applied after --json-mirror-only). Repeatable."
        ),
    )


def run(args: argparse.Namespace) -> int:
    """Execute the ``fetch`` subcommand."""
    if not args.parts and not args.family and not args.all:
        print("error: provide one or more PARTs, or use --family or --all", end="\n")
        return 2

    variants = [args.variant] if args.variant else list(DEFAULT_SDK_VARIANTS)
    output_root = args.output if args.output is not None else Path.cwd()

    # Progress UI goes to stderr so stdout stays a clean list of paths.
    console = Console(stderr=True)
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    results: list[dict] = []
    with progress:
        if args.all or args.family:
            exit_code = _run_bulk(args, variants, output_root, progress, results)
        else:
            exit_code = _run_parts(args, variants, output_root, progress, results, console)

    # Post-run summary table on stderr (text mode and json mode both get this).
    if results:
        _print_summary_table(console, results)

    # Always emit per-part JSON sidecars next to the mirrored XML tree, so
    # `<PART>/` carries both the raw NXP binders under `xml/` and the
    # agent-friendly chip-data + roadmap views under `json/`. `--json`
    # only controls stdout shape (summary path vs mirror paths).
    for part_name, payload in _per_part_details(results).items():
        target_dir = output_root / part_name / "json"
        write_json(target_dir / f"{part_name}.json", payload)

    # Full XML->JSON mirror (default): every <variant>/.../foo.xml becomes
    # <PART>/json/<variant>/.../foo.json, so an agent that only speaks
    # JSON can reach every byte of the silicon data. Suppressible with
    # --no-json-mirror; narrowable via --json-mirror-only/skip globs.
    # Progress UI on stderr, one bar per (part, variant).
    mirror_stats = _mirror_xml_to_json(args, results, output_root, console)

    # Per-part roadmap last, so it can report the mirror layout actually
    # produced for this run.
    for part_name, roadmap in _per_part_roadmaps(results, mirror_stats).items():
        target_dir = output_root / part_name / "json"
        write_json(target_dir / f"{part_name}.roadmap.json", roadmap)

    if args.json:
        # Top-level invocation summary in CWD; stdout = summary path.
        summary = {
            "output_root": output_root,
            "count": len(results),
            "results": results,
        }
        summary_path = write_json(Path.cwd() / "fetch.json", summary)
        print(summary_path)
    else:
        for r in results:
            print(r["mirror_path"])

    return exit_code


def _run_bulk(
    args: argparse.Namespace,
    variants: list[str],
    output_root: Path,
    progress: Progress,
    results: list[dict],
) -> int:
    """Handle ``--all`` and ``--family`` selections."""
    start_count = len(results)
    for variant in variants:
        def make_cb(part: str, variant_: str):
            return _make_progress_cb(progress, part, variant_)

        cache_paths = fetch_all(
            family=args.family,
            variant=variant,
            version=args.version,
            force=args.force,
            progress_factory=make_cb,
        )
        for cache_path in cache_paths:
            mirror_path = mirror_part_variant(cache_path, output_root)
            results.append(_summarize(cache_path, mirror_path))
    return _result_exit_code(results, start_count)


def _run_parts(
    args: argparse.Namespace,
    variants: list[str],
    output_root: Path,
    progress: Progress,
    results: list[dict],
    console: Console,
) -> int:
    """Handle positional ``PART`` selection."""
    start_count = len(results)
    client = KexClient()
    selected_parts = _resolve_requested_parts(args.parts, client, console)
    if not selected_parts:
        return 1

    for part in selected_parts:
        for variant in variants:
            cb = _make_progress_cb(progress, part, variant)
            try:
                cache_path = fetch(
                    part,
                    variant=variant,
                    version=args.version,
                    force=args.force,
                    client=client,
                    progress_callback=cb,
                )
            except NxpFetchError as exc:
                # A part may not publish every variant; skip quietly when
                # the user asked for the "all variants" default. Surface
                # the error when the user explicitly asked for the variant.
                if args.variant is not None:
                    print(f"error: {part}/{variant}: {exc}", file=sys.stderr)
                    return 1
                continue
            mirror_path = mirror_part_variant(cache_path, output_root)
            results.append(_summarize(cache_path, mirror_path))

    return _result_exit_code(results, start_count)


def _resolve_requested_parts(
    raw_parts: list[str],
    client: KexClient,
    console: Console,
) -> list[str]:
    """Expand positional part inputs into canonical NXP portfolio keys."""
    portfolio = client.portfolio_latest_map()
    selected: list[str] = []
    unresolved: list[str] = []
    for raw_part in raw_parts:
        matches = portfolio_part_matches(raw_part, portfolio.keys())
        if not matches:
            unresolved.append(raw_part)
            continue
        _print_resolution_note(console, raw_part, matches)
        selected.extend(matches)

    if unresolved:
        for raw_part in unresolved:
            print(f"error: unknown processor family or part: {raw_part}", file=sys.stderr)
        return []
    return _dedupe_parts(selected)


def _print_resolution_note(console: Console, raw_part: str, matches: list[str]) -> None:
    """Print a stderr note when positional input is expanded or normalized."""
    if len(matches) > 1:
        console.print(
            f"{raw_part} matched {len(matches)} processor parts; expanding prefix"
        )
    elif matches[0].lower() != raw_part.lower():
        console.print(f"{raw_part} resolved to {matches[0]}")


def _dedupe_parts(parts: list[str]) -> list[str]:
    """Return ``parts`` with case-insensitive duplicates removed in order."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return out


def _result_exit_code(results: list[dict], start_count: int) -> int:
    """Return a fetch exit code based on whether this selection produced rows."""
    if len(results) > start_count:
        return 0
    print("error: no variants fetched for the requested selection", file=sys.stderr)
    return 1


def _summarize(cache_path: Path, mirror_path: Path) -> dict:
    """Build a per-(part, variant) result dict for the run summary."""
    # cache_path is .../kex/<version>/processors/<PART>/<VARIANT>/
    version = cache_path.parents[2].name
    part = cache_path.parent.name
    variant = cache_path.name
    files = 0
    size_bytes = 0
    for p in mirror_path.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size_bytes += p.stat().st_size
            except OSError:
                continue
    return {
        "part": part,
        "variant": variant,
        "source_version": version,
        "cache_path": cache_path,
        "mirror_path": mirror_path,
        "files": files,
        "size_bytes": size_bytes,
    }


def _per_part_details(results: list[dict]) -> dict[str, object]:
    """Build a PartDetails JSON view for each unique part in ``results``.

    Prefers the canonical ksdk2_0 variant when present; falls back to the
    first variant seen for that part. Returns a mapping ``part -> payload``.
    """
    by_part: dict[str, list[dict]] = {}
    for r in results:
        by_part.setdefault(r["part"], []).append(r)

    out: dict[str, object] = {}
    for part, rows in by_part.items():
        chosen = next(
            (r for r in rows if r["variant"] == DEFAULT_VARIANT),
            rows[0],
        )
        try:
            info = details_from_cache(
                part,
                variant=chosen["variant"],
                version=chosen["source_version"],
            )
        except NxpFetchError as exc:
            out[part] = {"error": str(exc), "results": rows}
            continue
        out[part] = {
            "details": info,
            "fetched_variants": [
                {
                    "variant": r["variant"],
                    "source_version": r["source_version"],
                    "mirror_path": r["mirror_path"],
                    "files": r["files"],
                    "size_bytes": r["size_bytes"],
                }
                for r in rows
            ],
        }
    return out


def _mirror_xml_to_json(
    args: argparse.Namespace,
    results: list[dict],
    output_root: Path,
    console: Console,
) -> dict[tuple[str, str], int]:
    """Walk each mirrored variant tree and emit the parallel JSON tree.

    Drives a rich progress bar on ``console`` (stderr) with one task
    per (part, variant) showing files-converted / files-total. Returns
    a mapping ``(part, variant) -> json_file_count`` reporting how many
    JSON files were written for each variant. When ``--no-json-mirror``
    is set, the mapping is empty.
    """
    if args.no_json_mirror or not results:
        return {}
    include = args.json_mirror_only
    exclude = args.json_mirror_skip
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    stats: dict[tuple[str, str], int] = {}
    with progress:
        for r in results:
            xml_root = r["mirror_path"]  # output_root/<PART>/xml/<VARIANT>/
            json_root = output_root / r["part"] / "json" / r["variant"]
            cb = _make_mirror_cb(progress, r["part"], r["variant"])
            count = mirror_xml_tree_as_json(
                xml_root,
                json_root,
                include=include,
                exclude=exclude,
                progress_callback=cb,
            )
            stats[(r["part"], r["variant"])] = count
    return stats


def _make_mirror_cb(progress: Progress, part: str, variant: str):
    """Build a per-(part, variant) callback for the XML->JSON mirror phase.

    First call adds a rich task sized to the file total; subsequent
    calls advance it.
    """
    task_id: list[TaskID | None] = [None]
    label = f"{part}/{variant} -> JSON"

    def update(done: int, total: int, rel: str) -> None:
        if task_id[0] is None:
            task_id[0] = progress.add_task(label, total=total)
            return
        progress.update(task_id[0], completed=done)

    return update


def _per_part_roadmaps(
    results: list[dict],
    mirror_stats: dict[tuple[str, str], int],
) -> dict[str, dict]:
    """Build a per-part roadmap dict for each unique part in ``results``.

    Prefers the canonical ksdk2_0 variant's mirrored tree when present;
    falls back to the first variant seen for that part. Each roadmap is
    decorated with a ``layout`` block describing the ``<PART>/`` folder
    split (``xml/`` raw binders vs ``json/`` parsed sidecars + mirror)
    so an agent picking up just the JSON file understands the
    surrounding on-disk layout. Returns a mapping ``part -> roadmap_dict``.
    """
    by_part: dict[str, list[dict]] = {}
    for r in results:
        by_part.setdefault(r["part"], []).append(r)
    out: dict[str, dict] = {}
    for part, rows in by_part.items():
        chosen = next(
            (r for r in rows if r["variant"] == DEFAULT_VARIANT),
            rows[0],
        )
        roadmap = build_roadmap(chosen["mirror_path"])
        roadmap["layout"] = _part_folder_layout(part, rows, mirror_stats)
        out[part] = roadmap
    return out


def _part_folder_layout(
    part: str,
    rows: list[dict],
    mirror_stats: dict[tuple[str, str], int],
) -> dict:
    """Describe the ``<PART>/`` part-folder split emitted by ``fetch``."""
    variants = sorted({r["variant"] for r in rows})
    mirror_present = any(mirror_stats.get((part, v), 0) > 0 for v in variants)
    entries: dict[str, str] = {
        "xml/": (
            "Raw NXP MCUXpresso Config Tools binders, one subdir per "
            "SDK variant. Same silicon under each variant; only the "
            "output binder differs. ksdk2_0 is canonical for silicon "
            "facts (registers, pins, clocks, packages). zephyr3_2 "
            "carries Zephyr DT codegen scripts. i_mx_2_0 is the "
            "i.MX Linux binder."
        ),
        f"json/{part}.json": (
            "Parsed PartDetails spine: header (vendor, family, series, "
            "enabled tools, default part variant), CPU cores, "
            "per-package SKU variants with db_link manifests, and "
            "processor.properties. Use this instead of parsing the "
            "XML for top-level chip facts."
        ),
        f"json/{part}.roadmap.json": (
            "This file. Agent guide + inferred-schema namespaces for "
            "the canonical variant tree, plus this layout block."
        ),
    }
    if mirror_present:
        entries["json/<variant>/"] = (
            "Parallel XML->JSON mirror: every <variant>/.../foo.xml "
            "under xml/ has a corresponding foo.json here. "
            "Namespace-stripped tags; attributes as '@name'; mixed text "
            "as '#text'; repeated children as lists; root '@_xmlns' "
            "preserves the source namespace declarations. Lossless for "
            "the data the LLM cares about."
        )
    return {
        "part_folder": f"{part}/",
        "entries": entries,
        "xml_variants_present": variants,
        "canonical_variant_for_silicon_facts": DEFAULT_VARIANT,
        "json_mirror": {
            "enabled": mirror_present,
            "files_written": {
                f"{p}/{v}": n for (p, v), n in mirror_stats.items() if p == part
            },
        },
    }


def _print_summary_table(console: Console, results: list[dict]) -> None:
    """Print a rich table summarizing all fetched (part, variant) rows."""
    table = Table(title="fetch summary", title_justify="left")
    table.add_column("part", style="bold")
    table.add_column("variant")
    table.add_column("version")
    table.add_column("files", justify="right")
    table.add_column("size", justify="right")
    table.add_column("mirror path", overflow="fold")
    total_files = 0
    total_bytes = 0
    for r in results:
        total_files += r["files"]
        total_bytes += r["size_bytes"]
        table.add_row(
            r["part"],
            r["variant"],
            r["source_version"],
            str(r["files"]),
            _human_bytes(r["size_bytes"]),
            str(r["mirror_path"]),
        )
    table.add_section()
    table.add_row(
        f"[bold]{len({r['part'] for r in results})} parts[/bold]",
        f"{len(results)} variants",
        "",
        str(total_files),
        _human_bytes(total_bytes),
        "",
    )
    console.print(table)


def _human_bytes(n: int) -> str:
    """Format ``n`` bytes as a short human-readable string."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _make_progress_cb(progress: Progress, part: str, variant: str):
    """Build a per-(part, variant) progress callback bound to ``progress``.

    Returns a callable matching :data:`nxp_monkey.kex_client.ProgressCallback`:
    on the first call it adds a rich task, subsequent calls advance it.
    """
    task_id: list[TaskID | None] = [None]

    def update(bytes_so_far: int, total: int | None) -> None:
        if task_id[0] is None:
            task_id[0] = progress.add_task(
                f"{part}/{variant}", total=total if total is not None else None
            )
            return
        progress.update(task_id[0], completed=bytes_so_far, total=total)

    return update
