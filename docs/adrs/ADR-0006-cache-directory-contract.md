# ADR-0006: Cache Directory Contract

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` fetches XML metadata, JSON, and ZIP archives from NXP's public
KEX storage API. Fetches are expensive (multi-MB ZIP archives per processor
family per SDK variant) and the upstream content is effectively immutable per
version. A local cache on disk is required for:

- responsive search and indexing (no re-fetch on every command);
- offline analysis by LLM agents and downstream tooling;
- reproducible data state across machines.

Cache layout is a public compatibility surface: downstream tools and users
will read directly from these paths.

## Decision

### Location

The cache root is resolved via `platformdirs.user_cache_dir("nxp_monkey",
appauthor=False)`. No vendor parent directory is inserted on Windows:

| OS | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\nxp_monkey\Cache` |
| macOS | `~/Library/Caches/nxp_monkey` |
| Linux | `$XDG_CACHE_HOME/nxp_monkey` or `~/.cache/nxp_monkey` |

The library exposes `nxp_monkey.cache_path() -> pathlib.Path` as the public
API for resolving this location.

v0.1 does not support overriding the cache directory via environment variable
or argument. A future ADR may introduce an override knob; until then the
single contract simplifies downstream tooling.

### Layout

```
<cache_root>/
  kex/
    versions.json                          # cached api version list (TTL)
    portfolio.json                         # family -> last-seen-version map
    <version>/
      processors/<FAMILY>/<variant>/...    # unpacked storage tree
      _zips/<FAMILY>_<variant>.zip         # raw archives (optional)
  index/
    nxp_monkey_index.sqlite                # FTS5 + part / family / variant table
    nxp_monkey_index.meta.json             # index build time, source version
```

The unpacked `processors/<FAMILY>/<variant>/` layout mirrors the on-disk
layout shipped by MCUXpresso Config Tools' Data Manager, so existing tooling
that reads `mcu_data_<version>/processors/...` works unchanged.

### TTLs

- `versions.json`: 24 hours. `KexClient` re-fetches when the cached file is
  older than the TTL. `--refresh` forces a re-fetch.
- `portfolio.json`: 24 hours, same semantics.
- Per-version processor data: no TTL. Treated as immutable once written.
- Index database: rebuilt explicitly by `nxp-monkey index build`. The library
  does not auto-rebuild.

### Operations

The library provides:

- `nxp_monkey.cache_path() -> Path`
- `nxp_monkey.cache_size() -> int` (bytes)
- `nxp_monkey.cache_clear(scope: str = "all")` — `"all"`, `"versions"`,
  `"index"`, or `"<version>"`.

The CLI exposes `nxp-monkey cache {path,size,clear}` wrappers.

## Consequences

- Downstream tools (megamaid, agent workflows) can rely on the cache path and
  layout being stable.
- Cache is on the user profile by default; this is appropriate for a
  per-user developer tool. Server / multi-user contexts will need an explicit
  override mechanism in a future ADR.
- Deleting the cache must never lose user-owned data — only re-fetchable
  upstream content lives under the cache root.
