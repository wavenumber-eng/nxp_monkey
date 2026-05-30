# ADR-0007: KEX Upstream And Version Resolution

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` consumes NXP's public KEX storage API, which is also used by the
MCUXpresso Config Tools Data Manager. The endpoints and content shape are not
formally documented by NXP, but their behavior has been observed and is
stable enough to depend on as long as we document the contract and isolate
the upstream specifics in one place.

There are several discovered behaviors that must be encoded:

- the tool id for MCUXpresso Configuration Tools is `2`;
- the `npidb/versions/<tool_id>` endpoint returns XML listing published API
  versions, some with empty `version` strings (unpublished);
- the `storage/<tool_id>/<version>/<path>` endpoint serves directory listings
  (`?cmd=dir`) and directory archives (`?cmd=getdir&recursive=true` returning
  ZIP);
- NXP does not publish every processor family in every tool release.
  Effectively each family freezes on the last tool version that included it
  (for example JN5188 has frozen on an older release while MCXA families
  follow current releases). The Data Manager presents a merged view; we call
  this "portfolio-latest".

## Decision

### Upstream constants

- `TOOL_ID = 2` for MCUXpresso Configuration Tools.
- `NPIDB_BASE = "https://mcuxpresso.nxp.com/SWTools/npidb"`.
- `STORAGE_BASE = "https://mcuxpresso.nxp.com/SWTools/storage"`.
- `PROCESSORS_PATH = "/kex_tools/processors"`.

These are encoded in `nxp_monkey.kex_client` and are the only module allowed
to know upstream URL shapes.

### Version resolution

`KexClient` resolves the tool version on demand:

1. If a `version` is passed explicitly, use it.
2. Else, consult the cached `versions.json` if within TTL (ADR-0006).
3. Else, fetch from `npidb/versions/2`, write `versions.json`, and pick the
   highest published (non-empty `version`) entry as the default.

`KexClient.resolve_version_for_family(family)` walks the portfolio map
(family → newest version containing that family) so frozen families resolve
to their last published tool version automatically.

### User agent and timeouts

- User-Agent header: `nxp-monkey/<version> (+https://github.com/wavenumber-eng/nxp_monkey)`.
- Default HTTP timeout: 60 seconds for metadata, 600 seconds for archive
  downloads.
- Default retry count: 2 retries with exponential backoff capped at 30 s.

### ZIP extraction safety

All ZIP archive members are normalized to repository paths and rejected if
they escape the output directory (path traversal protection). This was a
property of the source script and is retained as a hard requirement of the
fetch implementation.

### Tracking upstream changes

If NXP changes endpoint paths, query parameter names, or the XML shape:

- the change is captured as a new ADR or an amendment to this one;
- `kex_client` is updated in one place;
- offline test fixtures are refreshed to match the new shape;
- a release note documents user-visible behavior changes.

## Consequences

- `kex_client` is the only module allowed to know upstream URL shapes; other
  modules go through it.
- Frozen-family handling is built into `resolve_version_for_family`, so users
  rarely need to pin versions manually.
- Network testing is opt-in (ADR-0003); the offline test stratum uses
  recorded fixtures under `tests/fixtures/kex/`.
