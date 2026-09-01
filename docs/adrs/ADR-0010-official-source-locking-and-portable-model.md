# ADR-0010: Official Source Locking And Portable Model Ownership

Status: accepted
Date: 2026-08-16

Amended: 2026-09-01 by ADR-0011. The v0 Git-only lock and its
`kex.status = not-used` records remain immutable. ADR-0011 supersedes this
ADR's program-wide AI prohibition for new work and authorizes a separate,
versioned KEX acquisition/model path for MCXA156 PAC and SVD generation.

## Context

The MCXA156 PAC and FRDM-MCXA156 Embassy increment needs reproducible facts
from two official NXP delivery systems:

- MCUXpresso Config Tools KEX data, which NXP Monkey already discovers,
  downloads, caches, and describes; and
- the official MCUXpresso SDK west manifest, its board selection policy, and
  the exact project revisions selected for a board or device.

The existing package boundary deliberately excludes downstream code generation
and board policy. It does not yet say who owns SDK-manifest resolution,
immutable cross-source locks, or a source-neutral silicon model. Leaving those
responsibilities implicit would make the MCXA156 output hard to replay and
would couple acquisition decisions to nxp-pac metadata.

The cross-repository program design is maintained in
`wavenumber-eng/wavenumber-eng-nxp-rust-enablement`. That repository retains
accepted source locks, generation manifests, evidence, and upstream status.

## Decision

### NXP Monkey boundary

NXP Monkey expands its library-first boundary to own:

- deterministic resolution of official NXP KEX and MCUXpresso SDK manifest
  inputs;
- acquisition and content-addressed local caching of the resolved inputs;
- verification of immutable source locks, including offline verification;
- conversion of resolved official facts into a versioned, portable normalized
  silicon/package/board model; and
- source comparison and conflict reports that preserve field-level provenance.

The package does not own nxp-pac metadata, register transforms, Rust code
generation, Embassy configuration, or board examples. A separately tested
nxp-pac adapter consumes the portable model and writes a separate generation
manifest.

### Public contract and staging

The contracts are versioned independently. Version zero assigns serialized
ownership explicitly: the coordination repository owns source-lock,
generation-manifest, and override-set schemas; NXP Monkey owns the
normalized-model schema and its migration policy.
`docs/design/source-lock-and-model.html` defines their interface. NXP Monkey
implements source-lock and normalized-model contracts behind library-first APIs. CLI commands remain thin
wrappers and are added to the public command/interface manifests only in the
implementation slice that makes them usable.

The v0 replay command surface is:

```powershell
nxp-monkey source resolve `
  --manifest-url https://github.com/nxp-mcuxpresso/mcuxsdk-manifests.git `
  --manifest-revision <40-hex-commit> `
  --board <board-id> --device <device-id> --profile-spec <profile.json> `
  --resolver-revision <40-hex-commit> `
  --kex-policy not-used --cache <cache-directory> --output <lock.json>

nxp-monkey source verify `
  --lock <lock.json> --cache <cache-directory> --offline

nxp-monkey model normalize `
  --lock <lock.json> --cache <cache-directory> --offline --output <model.json>

nxp-monkey model compare `
  --left <model-or-adapter-output> --right <model-or-adapter-output> `
  --output <report.json>
```

`--manifest-revision` accepts only an exact commit. `--kex-policy not-used` is
the only value represented by source-lock schema v0; ADR-0011 uses a separate
successor acquisition contract rather than reinterpreting this schema. Recorded command
templates represent operational paths only with the literal tokens `${CACHE}`,
`${PROFILE}`, `${LOCK}`, and `${OUTPUT}`; host paths never enter canonical content. Resolve writes
only the requested lock; verify emits no modified lock. `--offline` forbids all
DNS, HTTP, and git fetch operations and fails if any required object or blob is
absent. The implementation slice may add presentation flags but may not weaken
these replay semantics without amending this ADR.

### Manifest selection oracle

Resolution starts from an exact 40-hex commit of
`nxp-mcuxpresso/mcuxsdk-manifests`. A tag or moving branch may be recorded as a
human label but is never the immutable identity. NXP Monkey invokes the pinned
manifest's own `west update_board --set ... --list-repo` behavior and west's
manifest parser/freeze behavior as the selection oracle. It does not maintain
an independent implementation of recursive west import or group-filter
semantics.

For the v26.06.00-LTS baseline, every active project revision is an exact
commit, so v0 records `west manifest --resolve --active-only` as the frozen
active-manifest output without cloning unrelated repositories. The four
inaccessible moving revisions are in excluded groups; the complete inventory
marks them `unavailable-inactive` with no invented commit, and the resolver
forbids selecting or consuming them. A future baseline with a moving active
revision requires exact resolution or a contract revision.

The lock records the complete parsed project inventory, the full board
reference closure (including optional projects), and the narrower consumed
input closure separately. Every selected project records both the manifest
revision and resolved commit OID.

### Determinism and identity

Locks and models use UTF-8 JSON, lexicographically sorted object keys, two-space
indentation, LF line endings, and one terminal newline. Arrays whose order is
not semantically meaningful are sorted by schema-defined stable keys. No wall
clock, cache path, host name, or temporary directory is part of canonical
content.

The lock ID is `sha256:<lowercase-hex>` over the canonical lock with `lock_id`
omitted. The normalized model, generation manifest, and override-set IDs use
the same rule with `model_id`, `manifest_id`, or `override_set_id` omitted.
The filename contains the full digest. Generator, adapter, override, and output
identity is deliberately excluded from a source lock and belongs in a separate
generation manifest. The design contract enumerates array sort keys, command
placeholders, value fingerprints, and the IP semantic-hash projection.

The required `fact_provenance` index maps each carried leaf JSON Pointer in a
model to one or more source records. Record-level `provenance_refs` are useful
grouping metadata but do not replace this field-level coverage invariant.

Two independently empty-cache resolutions of each device's explicit inputs
must be byte-identical. Offline verification must validate schema version,
lock ID, the replay-derived selected inventory, every cache-allowed consumed
commit/content hash, the cached profile, and every license-policy reference
without network access.

### Model layering and source precedence

The portable model keeps four independently versioned layers:

1. peripheral IP/register-block schemas identified by semantic hash;
2. derivative topology (cores, memories, instances, interrupts, DMA,
   clocks/resets, flash constraints, and capabilities);
3. package/SKU pin and feature availability; and
4. board wiring/resources.

There is no universal "preferred NXP source." Precedence is field-class and
scope specific:

- exact-SKU/package records override family-level availability claims when
  their source class is permitted; v0 retains open-source package identifiers
  but marks per-package bond-out unavailable rather than inferring it;
- CMSIS headers and startup/linker material are primary for derivative
  interrupt and memory integration facts;
- licensed SVD/register descriptions are primary for register shape;
- under ADR-0011, exact-package KEX data is an authorized primary candidate for
  register, interrupt, DMA, clock, pin-mux, and package facts in new work; KEX
  remains absent from historical v0 source locks;
- open-licensed board BSP records and retained hardware observations are
  primary for v0 board wiring; account-gated schematic/design content is not a
  v0 input without separate permission; and
- the pinned SDK manifest is primary for project selection and revisions.

Agreement is retained as corroboration. A disagreement is never silently
resolved by ordering: normalization emits a stable conflict and fails until an
evidence-backed scoped override is reviewed. Missing required v0 facts also
fail closed.

### Licensing and retention

Each source and derived-artifact class has separate `retain`, `cache`,
`redistribute`, `generate`, and `ai_input` dispositions with an evidence locator. The
default for a missing or ambiguous disposition is deny.

- Public MCUXpresso Git repositories may be retained or redistributed only to
  the extent allowed by their repository SBOM, file-level SPDX markers, and
  governing license. The manifest repository's BSD-3-Clause license does not
  automatically license every selected project or file.
- Board selection does not authorize repository caching. Resolution fetches
  only repositories containing declared consumed files with `cache = allow`;
  other selected project identities are replayed from the pinned manifest
  without downloading their repositories.
- Public/account-gated NXP data sheets, schematics, and design archives do not
  carry an open-source grant for this workflow and are excluded from v0. Their
  absence is represented explicitly: per-package bond-out is unavailable, and
  BSP-derived board wiring requires later hardware confirmation.
- Historical v0 work interpreted the Config Tools agreement conservatively and
  recorded KEX as `not-used`. ADR-0011 records the repository owner's corrected
  program direction: current NXP Monkey JSON/XML may be acquired, cached,
  inspected by AI-assisted tooling, normalized, and used to generate PAC, SVD,
  and validation artifacts. This permission does not automatically authorize
  committing or redistributing raw payloads. New locks record exact KEX
  versions and content hashes; historical v0 locks remain unchanged.
- Unknown, proprietary, conflicting, or AI-restricted material cannot become a
  committed fixture. Cache-only handling is permitted only when the governing
  license allows the intended operation.

The repository previously labeled three XML test files as captured KEX
payloads and shipped them in source distributions, and retained a KEX-derived
XML survey. This change removes the survey and replaces all three files with
independently authored synthetic fixtures. The current tree and future source
distributions therefore carry no intentionally KEX-derived fixture or report.
Published Git history remains an administrative/legal-owner remediation item;
this ADR does not authorize rewriting shared history or make a licensing claim
about prior releases.

Repository-owner disposition for the historical blobs remains unchanged: do
not rewrite published Git refs or withdraw prior releases without a separately
scoped decision. ADR-0011 removes the blanket agent prohibition for current,
authorized KEX acquisitions. Historical removed fixtures remain legacy
material rather than a preferred source and are inspected only when a task
explicitly requires historical analysis.

This is an engineering retention policy, not a conclusion that a broad NXP
license applies. Every actual source lock carries the reviewed policy revision
and the file-level license evidence used for that resolution.

## Consequences

- NXP Monkey gains explicit source-resolution and portable-model APIs without
  becoming a PAC generator.
- west is an optional source-resolution tool dependency, isolated from KEX-only
  workflows.
- The coordination repository is the retention boundary for accepted locks,
  generation manifests, compatibility reports, and hardware evidence.
- Existing `fetch`, `details`, index, and cache behavior remains compatible.
- Adding the public source/model APIs requires matching design docs, contract
  manifest entries, offline fixtures, and Rack tests under ADR-0005.
- Schema-breaking changes require a new schema version and an explicit
  migration; silent reinterpretation of existing locks or models is forbidden.
- Normalized-model schema v0 is retained unchanged. Schema v1 has no lossy
  serialized-model migration because its new source/policy distinctions require
  original evidence; callers must re-normalize the immutable v0 source lock.
