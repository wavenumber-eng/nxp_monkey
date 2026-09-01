# Agent Guide

`nxp-monkey` is a public Python package for NXP MCUXpresso Config Tools KEX
data discovery, fetch, indexing, and local cache inspection. Keep changes
focused on stable CLI behavior, library-first APIs, and public documentation
contracts.

## KEX Data Boundary

ADR-0011 authorizes AI-assisted use of current NXP Monkey JSON/XML for the NXP
Rust enablement program. Agents may fetch, inspect, compare, normalize, and use
current KEX data to generate source locks, portable models, CMSIS-SVD, PAC
metadata, Rust PAC output, and validation reports when the task is in scope.

- Record the exact NXP Monkey revision/version, requested part, upstream data
  version, variants, acquisition command, and hashes of every consumed file.
- Keep raw fetched payloads in the local cache or an explicitly designated
  untracked workspace. Do not commit or redistribute raw payloads unless a
  separate retained disposition permits that action.
- Generated artifacts and reports must retain field-level provenance and must
  distinguish source facts from adapter policy.
- Network-marked KEX tests may run when needed for an authorized acquisition or
  replay slice. Never expose credentials, tokens, customer data, or unrelated
  cached parts.
- Treat historical removed fixtures and surveys as unrelated legacy material;
  inspect them only when a task explicitly requires historical analysis.

Source-lock schema v0 remains an immutable Git-only contract whose KEX value is
`not-used`. New KEX acquisition identity belongs to a versioned successor
contract; do not silently reinterpret existing v0 locks.

## Setup

Use `uv` for local development:

```bash
uv sync --all-extras
```

Commit `uv.lock`. Do not hand-edit it.

## Test And Signoff

Run the package signoff before release-facing changes:

```bash
uv run rack run --all
uv run python -m build
uv run twine check dist/*
uv run python tests/support_scripts/install_test.py
```

## Architecture Boundaries

- NXP KEX transport and cache behavior belongs in library modules.
- CLI modules are thin wrappers that parse arguments, call library APIs, and
  format output.
- Public commands and public interfaces must be listed in `docs/contracts/`
  and have matching design docs under `docs/design/`.
- `docs/research/` is working/reference material and is excluded from source
  distributions unless promoted into a durable public docs location.

## Release Rules

- `main` should represent the latest released/tagged source.
- Public changes should merge through PRs with required CI.
- Release publication should trigger validation and trusted PyPI publishing.
- Date-based versions are standard, for example `2026.6.4`.
- `CHANGELOG.md` and `docs/releases/<YYYY-MM-DD>.md` must mention the current
  package version.

## Local Secrets

Do not commit `.env` files, PyPI tokens, private corpora, customer data, or
downloaded proprietary SDK bundles. PyPI publishing should use trusted
publishing.

## Exceptions

Strict rules are the target. Current exceptions must be documented in
`docs/contracts/exceptions.json` and should ratchet down over time.
