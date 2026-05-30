"""HTTP client for NXP's public MCUXpresso Config Tools KEX storage API.

This module is the only place in the package that knows upstream URL shapes.
See ADR-0007 for the upstream contract and version-resolution rules.

The KEX endpoints used here:

- ``GET {NPIDB_BASE}/versions/2`` — XML list of tool versions.
- ``GET {STORAGE_BASE}/2/{version}{path}?cmd=dir`` — XML directory listing.
- ``GET {STORAGE_BASE}/2/{version}{path}?cmd=getdir&recursive=true`` — ZIP
  archive of a directory tree.

The public class :class:`KexClient` wraps these with cached version
resolution and offline-friendly defaults. Free functions at the bottom of
this module provide a stateless interface for code that does not want to
manage a client instance.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from xml.etree import ElementTree

from . import cache
from ._version import __version__
from .models import ApiVersion, StorageEntry

#: Callable invoked during a long download with ``(bytes_so_far, total_or_None)``.
ProgressCallback = Callable[[int, int | None], None]

TOOL_ID = 2
NPIDB_BASE = "https://mcuxpresso.nxp.com/SWTools/npidb"
STORAGE_BASE = "https://mcuxpresso.nxp.com/SWTools/storage"
PROCESSORS_PATH = "/kex_tools/processors"
USER_AGENT = (
    f"nxp-monkey/{__version__} (+https://github.com/wavenumber-eng/nxp_monkey)"
)
DEFAULT_TIMEOUT_S = 60
DEFAULT_DOWNLOAD_TIMEOUT_S = 600
DEFAULT_RETRIES = 2
VERSIONS_TTL_S = 24 * 60 * 60  # 24 hours
PORTFOLIO_TTL_S = 24 * 60 * 60


class NxpFetchError(RuntimeError):
    """Raised when the KEX storage API cannot be fetched or parsed."""


def _version_key(value: str) -> tuple[int, ...]:
    """Return a tuple suitable for sorting dotted version strings."""
    parts: list[int] = []
    for piece in value.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


def _normalize_remote_path(path: str) -> str:
    """Normalize a KEX storage path to leading-slash form."""
    if not path:
        return "/"
    clean = "/" + path.strip("/")
    return "/" if clean == "/" else clean


def _storage_url(
    version: str,
    path: str,
    cmd: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Build a KEX ``storage`` URL for the given version, path, and command."""
    normalized = _normalize_remote_path(path)
    query = {"cmd": cmd}
    if params:
        query.update(params)
    encoded_path = parse.quote(normalized, safe="/")
    return (
        f"{STORAGE_BASE}/{TOOL_ID}/{parse.quote(version, safe='.')}"
        f"{encoded_path}?{parse.urlencode(query)}"
    )


def _versions_url() -> str:
    """Build the KEX ``npidb/versions`` URL for tool id 2."""
    return f"{NPIDB_BASE}/versions/{TOOL_ID}"


def _request(url: str) -> request.Request:
    """Build a :class:`urllib.request.Request` with the package user agent."""
    return request.Request(url, headers={"User-Agent": USER_AGENT})


def _read_url_bytes(url: str, *, timeout_s: int = DEFAULT_TIMEOUT_S) -> bytes:
    """Fetch ``url`` and return its raw body bytes."""
    req = _request(url)
    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            return response.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise NxpFetchError(f"{exc.code} {exc.reason} for {url}: {body[:500]}") from exc
    except error.URLError as exc:
        raise NxpFetchError(f"Request failed for {url}: {exc}") from exc


def _read_xml_url(
    url: str, *, timeout_s: int = DEFAULT_TIMEOUT_S
) -> ElementTree.Element:
    """Fetch ``url`` and return its body parsed as XML."""
    payload = _read_url_bytes(url, timeout_s=timeout_s)
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        sample = payload[:300].decode("utf-8", errors="replace")
        raise NxpFetchError(f"Invalid XML from {url}: {sample}") from exc


class KexClient:
    """Client for NXP's KEX storage API with on-disk caching.

    The client resolves tool versions lazily: callers can construct a client
    without specifying a version and the first call that needs one will
    consult the cache (within :data:`VERSIONS_TTL_S`) or fetch from
    ``npidb/versions``.

    Example:
        >>> client = KexClient()
        >>> client.list_versions()  # cached for 24h after first call
        >>> client.latest_version()
        >>> client.list_processor_families()  # uses latest by default
    """

    def __init__(
        self,
        version: str | None = None,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        download_timeout_s: int = DEFAULT_DOWNLOAD_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        include_unpublished: bool = False,
    ) -> None:
        """Construct a KEX client.

        Args:
            version: Optional explicit tool version to use. ``None`` means
                "resolve on demand from cache or upstream".
            timeout_s: HTTP timeout for metadata requests, in seconds.
            download_timeout_s: HTTP timeout for archive downloads, in
                seconds.
            retries: Number of retry attempts on transient download
                failures.
            include_unpublished: When True, allow ``latest_version`` to
                resolve to a tool version whose ``version`` field is empty.
        """
        self._explicit_version = version
        self._timeout_s = timeout_s
        self._download_timeout_s = download_timeout_s
        self._retries = retries
        self._include_unpublished = include_unpublished
        self._versions_cache: list[ApiVersion] | None = None
        self._portfolio_cache: dict[str, str] | None = None

    # ----- version handling ------------------------------------------------

    def list_versions(self, *, refresh: bool = False) -> list[ApiVersion]:
        """Return all tool versions known to the KEX ``npidb`` endpoint.

        Args:
            refresh: When True, bypass the on-disk cache and re-fetch.

        Returns:
            List of :class:`ApiVersion` sorted by version key, ascending.
        """
        if self._versions_cache is not None and not refresh:
            return list(self._versions_cache)

        path = cache.kex_root() / "versions.json"
        if not refresh and _is_fresh(path, VERSIONS_TTL_S):
            self._versions_cache = _load_versions(path)
            return list(self._versions_cache)

        root = _read_xml_url(_versions_url(), timeout_s=self._timeout_s)
        versions: list[ApiVersion] = []
        for node in root.findall("tool_version"):
            api_id_text = node.findtext("api_id", default="0").strip()
            try:
                api_id = int(api_id_text)
            except ValueError:
                api_id = 0
            versions.append(
                ApiVersion(
                    api_id=api_id,
                    name=node.findtext("name", default="").strip(),
                    version=node.findtext("version", default="").strip(),
                )
            )
        versions.sort(key=lambda item: _version_key(item.name))
        _store_versions(path, versions)
        self._versions_cache = versions
        return list(versions)

    def latest_version(self) -> str:
        """Return the highest published tool version name.

        Returns:
            Tool version name (for example ``"25.12.10"``).

        Raises:
            NxpFetchError: If no published version is available.
        """
        versions = self.list_versions()
        candidates = [
            v for v in versions if v.name and (self._include_unpublished or v.stable)
        ]
        if not candidates:
            raise NxpFetchError("No MCUXpresso tool versions returned by NXP")
        return max(candidates, key=lambda item: _version_key(item.name)).name

    def resolve_version(self, version: str | None = None) -> str:
        """Resolve a tool version following the rules in ADR-0007.

        Args:
            version: ``None`` or an explicit version string.

        Returns:
            A resolved tool version name.
        """
        if version is not None:
            return version
        if self._explicit_version is not None:
            return self._explicit_version
        return self.latest_version()

    def resolve_version_for_family(self, family: str) -> str:
        """Resolve the newest tool version that publishes ``family``.

        The lookup is case-insensitive; ``mcxa156``, ``MCXA156``, and
        ``McXa156`` all resolve to the same upstream entry.

        Args:
            family: Processor family directory name (for example
                ``"MCXA156"``).

        Returns:
            Tool version name carrying that family's data.

        Raises:
            NxpFetchError: If ``family`` is not present in any known
                version.
        """
        portfolio = self.portfolio_latest_map()
        canonical = self._lookup_canonical(family, portfolio)
        return portfolio[canonical]

    def canonicalize_family(self, family: str) -> str:
        """Return the case-correct family name as NXP publishes it.

        Args:
            family: User-supplied family name (any case).

        Returns:
            Canonical family name from the portfolio map.

        Raises:
            NxpFetchError: If no family matches case-insensitively.
        """
        return self._lookup_canonical(family, self.portfolio_latest_map())

    @staticmethod
    def _lookup_canonical(family: str, portfolio: dict[str, str]) -> str:
        """Find ``family`` in ``portfolio`` case-insensitively, return canonical key."""
        if family in portfolio:
            return family
        target = family.lower()
        for key in portfolio:
            if key.lower() == target:
                return key
        raise NxpFetchError(f"Unknown processor family: {family}")

    @property
    def version(self) -> str:
        """Resolve the client's working tool version, fetching if needed."""
        return self.resolve_version()

    # ----- portfolio -------------------------------------------------------

    def portfolio_latest_map(self, *, refresh: bool = False) -> dict[str, str]:
        """Build (or load) a map of family name to latest tool version.

        NXP does not publish every family in every release. The map reflects
        the merged "portfolio" view used by the MCUXpresso Data Manager.

        Args:
            refresh: When True, rebuild from upstream even if a fresh cached
                copy exists.

        Returns:
            Dict mapping family name to the newest tool version that
            publishes it.
        """
        if self._portfolio_cache is not None and not refresh:
            return dict(self._portfolio_cache)

        path = cache.kex_root() / "portfolio.json"
        if not refresh and _is_fresh(path, PORTFOLIO_TTL_S):
            self._portfolio_cache = json.loads(path.read_text(encoding="utf-8"))
            assert self._portfolio_cache is not None
            return dict(self._portfolio_cache)

        latest_by_family: dict[str, str] = {}
        for v in self.list_versions(refresh=refresh):
            if not v.name or (not v.stable and not self._include_unpublished):
                continue
            try:
                entries = self.list_processor_families(version=v.name)
            except NxpFetchError:
                continue
            for entry in entries:
                latest_by_family[entry.name] = v.name
        sorted_map = dict(
            sorted(latest_by_family.items(), key=lambda item: item[0].lower())
        )
        path.write_text(json.dumps(sorted_map, indent=2, sort_keys=True), encoding="utf-8")
        self._portfolio_cache = sorted_map
        return dict(sorted_map)

    # ----- storage listings ------------------------------------------------

    def list_storage_entries(
        self, path: str, *, version: str | None = None
    ) -> list[StorageEntry]:
        """List one storage directory.

        Args:
            path: KEX storage path (for example
                ``"/kex_tools/processors"``).
            version: Optional explicit tool version override.

        Returns:
            List of :class:`StorageEntry` in the order returned by NXP.
        """
        resolved = self.resolve_version(version)
        url = _storage_url(resolved, path, "dir")
        root = _read_xml_url(url, timeout_s=self._timeout_s)
        entries: list[StorageEntry] = []
        for node in root.findall("entry"):
            directory = (
                node.findtext("directory", default="false").strip().lower() == "true"
            )
            time_text = node.findtext("time", default="").strip()
            size_text = node.findtext("size", default="").strip()
            try:
                remote_time: int | None = int(time_text)
            except ValueError:
                remote_time = None
            try:
                size: int | None = int(size_text)
            except ValueError:
                size = None
            entries.append(
                StorageEntry(
                    file_id=node.findtext("file_id", default="").strip(),
                    full_path=node.findtext("full_path", default="").strip(),
                    directory=directory,
                    time=remote_time,
                    size=size,
                    compression=node.findtext("compression", default="").strip(),
                )
            )
        return entries

    def list_processor_families(
        self, *, version: str | None = None
    ) -> list[StorageEntry]:
        """List processor families for a tool version.

        Args:
            version: Optional explicit tool version override.

        Returns:
            Sorted list of family directory entries.
        """
        entries = self.list_storage_entries(PROCESSORS_PATH, version=version)
        return sorted(
            [entry for entry in entries if entry.directory and entry.name],
            key=lambda entry: entry.name.lower(),
        )

    def discover_family_variants(
        self,
        family: str,
        variants: list[str] | tuple[str, ...] | None = None,
        *,
        version: str | None = None,
    ) -> list[str]:
        """Return the SDK variants present for ``family`` at ``version``.

        Args:
            family: Processor family directory name.
            variants: Iterable of candidate variant names to probe. ``None``
                uses the default set ``("ksdk2_0", "zephyr3_2", "i_mx_2_0")``.
            version: Optional explicit tool version override.

        Returns:
            List of variant names actually present upstream.
        """
        from .fetch import DEFAULT_SDK_VARIANTS  # local import to avoid cycle

        resolved = self.resolve_version(version)
        candidates = list(variants) if variants is not None else list(DEFAULT_SDK_VARIANTS)
        available: list[str] = []
        for variant in candidates:
            remote_path = f"{PROCESSORS_PATH}/{family}/{variant}"
            try:
                entries = self.list_storage_entries(remote_path, version=resolved)
            except NxpFetchError:
                continue
            if entries:
                available.append(variant)
        return available

    # ----- downloads -------------------------------------------------------

    def download_directory_zip(
        self,
        family: str,
        variant: str,
        output_zip: Path,
        *,
        version: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> int:
        """Download the ZIP archive for ``processors/<family>/<variant>``.

        Args:
            family: Processor family directory name.
            variant: SDK variant name.
            output_zip: Destination file path for the archive.
            version: Optional explicit tool version override.
            progress_callback: Optional callable invoked as
                ``progress_callback(bytes_so_far, total_or_None)`` once with
                ``(0, total)`` before the first chunk and again after each
                1 MiB block. ``total`` is the response ``Content-Length`` if
                upstream reports it, otherwise ``None``.

        Returns:
            Number of bytes written.

        Raises:
            NxpFetchError: On repeated download failure.
        """
        resolved = self.resolve_version(version)
        remote_path = f"{PROCESSORS_PATH}/{family}/{variant}"
        url = _storage_url(resolved, remote_path, "getdir", {"recursive": "true"})
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            attempt += 1
            req = _request(url)
            try:
                with request.urlopen(req, timeout=self._download_timeout_s) as response:
                    total_header = response.headers.get("Content-Length")
                    try:
                        total: int | None = int(total_header) if total_header else None
                    except ValueError:
                        total = None
                    if progress_callback is not None:
                        progress_callback(0, total)
                    with output_zip.open("wb") as handle:
                        written = 0
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            written += len(chunk)
                            if progress_callback is not None:
                                progress_callback(written, total)
                return written
            except (error.HTTPError, error.URLError, TimeoutError) as exc:
                if output_zip.exists():
                    output_zip.unlink()
                if attempt > self._retries + 1:
                    raise NxpFetchError(
                        f"Download failed after {attempt} attempts for {url}: {exc}"
                    ) from exc
                time.sleep(min(2**attempt, 30))


# ---- file-level helpers ---------------------------------------------------


def _is_fresh(path: Path, ttl_s: int) -> bool:
    """Return True when ``path`` exists and is younger than ``ttl_s``."""
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl_s


def _store_versions(path: Path, versions: list[ApiVersion]) -> None:
    """Write a versions list to ``path`` as JSON."""
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "versions": [asdict(item) for item in versions],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_versions(path: Path) -> list[ApiVersion]:
    """Read a versions list from ``path`` previously written by this module."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ApiVersion(**item) for item in payload.get("versions", [])]


# ---- stateless convenience wrappers --------------------------------------


def list_versions(*, refresh: bool = False) -> list[ApiVersion]:
    """Stateless wrapper that constructs a :class:`KexClient` and lists versions."""
    return KexClient().list_versions(refresh=refresh)


def latest_version() -> str:
    """Stateless wrapper that returns the latest published tool version."""
    return KexClient().latest_version()


def list_families(version: str | None = None) -> list[StorageEntry]:
    """Stateless wrapper that lists processor families."""
    return KexClient(version=version).list_processor_families()


def portfolio_latest_map(*, refresh: bool = False) -> dict[str, str]:
    """Stateless wrapper that builds the portfolio-latest family map."""
    return KexClient().portfolio_latest_map(refresh=refresh)
