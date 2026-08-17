# Agent Guide

`nxp-monkey` is a public Python package for NXP MCUXpresso Config Tools KEX
data discovery, fetch, indexing, and local cache inspection. Keep changes
focused on stable CLI behavior, library-first APIs, and public documentation
contracts.

## KEX AI Boundary

NXP's current Config Tools license prohibits using the licensed software as
AI/model input. AI-assisted work in this repository must use only current-tree
fixtures explicitly marked fully synthetic or inputs whose open license and AI
disposition have been reviewed.

- Do not download, inspect, summarize, or expose live KEX responses, KEX cache
  contents, or KEX-derived reports to an AI agent.
- Do not run network-marked KEX tests in an AI-assisted session.
- Do not use `git show`, patch-producing `git log`, unscoped historical diffs,
  or equivalent commands to expose prior versions of `tests/fixtures/kex/**`,
  `docs/research/xml_survey.md`, or other removed KEX-derived blobs to an AI
  agent. Current-tree inspection of the fully synthetic fixtures is allowed.
- Historical cleanup, release withdrawal, and review of old KEX artifacts are
  human/repository-owner operations performed outside AI context. Use a
  sanitized export without those blobs if an AI task needs older source state.

These restrictions apply even when the data remains technically reachable in
public Git history. ADR-0010 records the owner disposition.

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
