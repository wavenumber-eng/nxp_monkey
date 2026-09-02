# ADR-0011: AI-Assisted KEX Generation

Status: accepted
Date: 2026-09-01

## Context

ADR-0010 conservatively excluded KEX payloads from the AI-assisted MCXA156
pipeline. On 2026-09-01, repository owner Eli Hughes identified that blanket
exclusion as a mistake and explicitly authorized use of NXP Monkey JSON/XML for
the NXP Rust enablement program. An exploratory `nxp-monkey fetch MCXA156`
showed that the current data contains versioned register, interrupt, DMA,
clock, package, pin, and mux structures needed for complete PAC generation and
for testing CMSIS-SVD reconstruction.

Permission to process source data and generate artifacts is distinct from
permission to publish unchanged raw source payloads. Reproducibility also
requires more than a moving KEX endpoint or a human-readable tool version.

## Decision

Current NXP Monkey JSON/XML is authorized as AI/model input for this program.
AI-assisted agents and tools may:

- discover and fetch the requested NXP part;
- inspect and compare raw XML, its schema-oriented JSON projection, and derived schema
  inventories;
- normalize source facts with field-level provenance;
- generate and validate portable models, CMSIS-SVD, PAC metadata, Rust PAC
  output, and semantic reports; and
- run scoped network tests needed to prove acquisition and replay behavior.

Every retained generation path records the exact NXP Monkey repository commit
and package version, requested part, upstream data version, returned variants,
command, endpoint/archive identity when available, source-file hashes, schema
namespace/version inventory, transformation identity, and output hashes. Two
clean acquisitions must be byte-identical or every upstream nondeterministic
field must be removed from canonical identity with a reviewed rationale.

Raw KEX XML/JSON remains in the local cache or an explicitly designated
untracked workspace by default. This ADR does not authorize committing or
redistributing unchanged raw payloads. A separate retained disposition is
required for that action. Generated PAC/SVD/model/report artifacts may be
retained and contributed when their generation manifest and downstream
repository policy permit it.

Source-lock schema v0 remains immutable and represents KEX only as
`not-used`. KEX acquisition and replay use a versioned successor contract. No
existing v0 lock is silently modified or reinterpreted.

NXP Monkey continues to stop at acquisition, normalized portable models, and
source comparison. CMSIS-SVD and nxp-pac adapters remain independently tested
downstream boundaries.

## Consequences

- The blanket KEX AI prohibition in ADR-0010, `AGENTS.md`, the README, and
  architecture documentation is superseded for current authorized work.
- The MCXA156 PAC/SVD reproduction plan may use the fetched 26.06 dataset.
- Raw-source publication remains fail-closed and separate from generation.
- Historical v0 evidence remains truthful: it did not consume KEX.
- A new acquisition-lock contract, complete schema inventory, and independent
  source-slice review are required before fetched data becomes durable evidence.
