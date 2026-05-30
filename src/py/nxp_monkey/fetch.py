"""High-level fetch operations for processor family data.

Public surface:

- :func:`fetch` — fetch a single part's data tree.
- :func:`fetch_all` — fetch every part in a family or in the full portfolio.

These functions wrap :class:`nxp_monkey.kex_client.KexClient` with on-disk
caching following ADR-0006 and ZIP-extraction safety from ADR-0007.
"""
from __future__ import annotations

import shutil
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from . import cache
from .kex_client import KexClient, NxpFetchError, ProgressCallback

DEFAULT_SDK_VARIANTS: tuple[str, ...] = ("ksdk2_0", "zephyr3_2", "i_mx_2_0")
DEFAULT_VARIANT = "ksdk2_0"


def fetch(
    part: str,
    *,
    variant: str = DEFAULT_VARIANT,
    version: str | None = None,
    force: bool = False,
    client: KexClient | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Fetch one part's data tree into the local cache.

    Args:
        part: Processor family / part identifier as it appears in the KEX
            storage tree (for example ``"MCXA156"``).
        variant: SDK variant to fetch. Defaults to ``"ksdk2_0"`` per
            ADR-0008.
        version: Optional explicit tool version. ``None`` resolves to the
            newest version that publishes ``part`` (portfolio-latest).
        force: When True, refetch even if a populated cache directory
            already exists.
        client: Optional :class:`KexClient` to reuse. A new client is
            constructed when omitted.
        progress_callback: Optional callable invoked as
            ``progress_callback(bytes_so_far, total_or_None)`` while the
            upstream ZIP is being downloaded. Not called when the cached
            tree already exists.

        Returns:
            Path to the unpacked ``processors/<part>/<variant>/`` tree in
            the cache.

    Raises:
        NxpFetchError: If the upstream archive is missing, empty, or
            unsafe to extract.
    """
    kex = client or KexClient()
    if version is not None:
        canonical_part = part
        resolved_version = version
    else:
        canonical_part = kex.canonicalize_family(part)
        resolved_version = kex.resolve_version_for_family(canonical_part)

    output_root = cache.kex_root() / resolved_version
    target_tree = output_root / "processors" / canonical_part / variant
    if target_tree.exists() and not force and any(target_tree.iterdir()):
        return target_tree

    zip_dir = output_root / "_zips"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"{canonical_part}_{variant}.zip"

    kex.download_directory_zip(
        canonical_part,
        variant,
        zip_path,
        version=resolved_version,
        progress_callback=progress_callback,
    )
    if not zipfile.is_zipfile(zip_path):
        sample = zip_path.read_bytes()[:300].decode("utf-8", errors="replace")
        raise NxpFetchError(
            f"NXP did not return a ZIP for {canonical_part}/{variant}: {sample}"
        )
    extracted = extract_processor_zip(zip_path, output_root)
    if extracted["files"] == 0:
        raise NxpFetchError(
            f"NXP returned an empty ZIP for {canonical_part}/{variant}"
        )
    return target_tree


def fetch_all(
    *,
    family: str | None = None,
    variant: str = DEFAULT_VARIANT,
    version: str | None = None,
    force: bool = False,
    client: KexClient | None = None,
    progress_factory: Callable[[str, str], ProgressCallback | None] | None = None,
) -> list[Path]:
    """Fetch every published part, optionally restricted to a family root.

    Args:
        family: Optional case-insensitive prefix to restrict the parts
            fetched (for example ``"MCXA"``). ``None`` fetches every
            part in the portfolio.
        variant: SDK variant to fetch. Defaults to ``"ksdk2_0"``.
        version: Optional explicit tool version. ``None`` uses
            portfolio-latest per ADR-0007.
        force: When True, refetch even when a populated cache directory
            exists.
        client: Optional :class:`KexClient` to reuse.
        progress_factory: Optional callable invoked with ``(part, variant)``
            for every (part, variant) about to be fetched; the returned
            callback (or ``None``) is forwarded to :func:`fetch` as
            ``progress_callback``.

    Returns:
        Paths to the unpacked trees of every part fetched.
    """
    kex = client or KexClient()
    portfolio = kex.portfolio_latest_map()
    parts = list(portfolio.keys())
    if family is not None:
        prefix = family.lower()
        parts = [p for p in parts if p.lower().startswith(prefix)]

    results: list[Path] = []
    for part in parts:
        cb = progress_factory(part, variant) if progress_factory else None
        try:
            results.append(
                fetch(
                    part,
                    variant=variant,
                    version=version,
                    force=force,
                    client=kex,
                    progress_callback=cb,
                )
            )
        except NxpFetchError:
            continue
    return results


def mirror_part_variant(cache_path: Path, output_root: Path) -> Path:
    """Copy a cached ``processors/<PART>/<VARIANT>/`` tree under ``output_root``.

    The destination is ``output_root/<PART>/xml/<VARIANT>/``; an existing
    tree at that path is replaced. ``output_root`` is created when missing.
    The ``xml/`` subdir separates the raw NXP binders from the parsed
    ``json/`` sidecars the CLI writes alongside (chip-data spine + roadmap).

    Args:
        cache_path: A ``processors/<PART>/<VARIANT>/`` directory as returned
            by :func:`fetch`.
        output_root: Destination root. The mirrored tree lives at
            ``output_root/<PART>/xml/<VARIANT>/``.

    Returns:
        Path to the mirrored tree.
    """
    variant_name = cache_path.name
    part_name = cache_path.parent.name
    target = output_root / part_name / "xml" / variant_name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cache_path, target)
    return target


def extract_processor_zip(zip_path: Path, output_root: Path) -> dict[str, int]:
    """Extract a KEX processor ZIP into ``output_root`` safely.

    The KEX ZIP archives contain top-level ``kex_tools/`` or ``processors/``
    prefixes; this function normalizes member paths to the ``processors/``
    layout and rejects any member that would escape ``output_root``
    (path-traversal protection).

    Args:
        zip_path: Source archive on disk.
        output_root: Destination root. ``processors/`` is created beneath
            this path.

    Returns:
        Dict with two keys, ``"files"`` and ``"dirs"``, counting items
        written.

    Raises:
        NxpFetchError: When a member is unsafe (path traversal).
    """
    file_count = 0
    dir_count = 0
    output_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            rel_path = _zip_relpath(member.filename)
            if rel_path is None:
                continue
            target = (output_root / rel_path).resolve()
            output_resolved = output_root.resolve()
            if output_resolved not in (target, *target.parents):
                raise NxpFetchError(
                    f"ZIP member escapes output directory: {member.filename}"
                )

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                dir_count += 1
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            file_count += 1
    return {"files": file_count, "dirs": dir_count}


def _zip_relpath(member_name: str) -> Path | None:
    """Normalize a KEX ZIP member path to start at ``processors/``.

    Returns ``None`` for empty paths. Raises :class:`NxpFetchError` for any
    member containing ``..`` segments.
    """
    raw_parts = [
        part
        for part in PurePosixPath(member_name.replace("\\", "/")).parts
        if part not in ("", ".", "/")
    ]
    if not raw_parts:
        return None

    if "processors" in raw_parts:
        raw_parts = raw_parts[raw_parts.index("processors") :]
    elif raw_parts[0] == "kex_tools":
        raw_parts = raw_parts[1:]

    if not raw_parts:
        return None
    if raw_parts[0] != "processors":
        raw_parts = ["processors", *raw_parts]
    if any(part == ".." for part in raw_parts):
        raise NxpFetchError(f"Unsafe ZIP member path: {member_name}")
    return Path(*raw_parts)


def part_iter(roots: Iterable[Path]) -> list[Path]:
    """Return every ``processors/<part>/<variant>/`` directory under ``roots``.

    Args:
        roots: Iterable of cache version roots (e.g. ``cache.kex_root() /
            "25.12.10"``).

    Returns:
        Sorted list of part-variant directories found.
    """
    found: list[Path] = []
    for root in roots:
        processors = root / "processors"
        if not processors.is_dir():
            continue
        for part_dir in processors.iterdir():
            if not part_dir.is_dir():
                continue
            for variant_dir in part_dir.iterdir():
                if variant_dir.is_dir():
                    found.append(variant_dir)
    return sorted(found)
