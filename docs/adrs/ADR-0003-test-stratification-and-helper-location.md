# ADR-0003: Test Stratification And Helper Location

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` needs predictable test layers so CI can run cheap checks fast and
defer expensive network-dependent checks. It also needs a clear convention for
where shared test helpers live so they do not leak into the public package.

## Decision

Tests are organized in named strata under `tests/`:

- `tests/L0_public_cli/` — fast smoke tests that exercise the installed CLI
  via subprocess. No network. Includes `--help`, `--version`, and basic
  argument validation for every public command. Must pass on every commit.

- `tests/L3_public_workflows/` — library workflow tests that exercise
  `import nxp_monkey` end-to-end against offline fixtures (cached XML/ZIP
  payloads under `tests/fixtures/`). No network. Must pass on every commit.

- `tests/L99_signoff/` — structural and conformance tests:
  - every public CLI command has a matching design HTML file with
    `data-command=` attribute;
  - every public interface listed in
    `docs/contracts/interface_design_manifest.v0.json` has a matching design
    HTML file with `data-interface=` attribute;
  - every public function has a non-empty PEP 257 docstring;
  - command manifest matches the live argparse tree;
  - JSON contracts validate against their schemas;
  - CLI command modules contain no business logic
    (no imports outside stdlib + argparse + the local package's library
    modules).

Tests that hit the live NXP API are reserved for an opt-in marker
(`@pytest.mark.network`) and are excluded from default runs.

Helpers and fixtures shared across tests live in:

- `tests/support_scripts/` — Python helper modules (not test files).
- `tests/fixtures/` — captured XML, ZIP, and JSON payloads used by
  offline tests.

`tests/support_scripts/` and `tests/fixtures/` are not Python packages and are
not part of the installed wheel. They ship in the sdist for reproducibility.

## Consequences

- The default `pytest` invocation runs L0 + L3 + L99 and is the gate for
  commits and PRs.
- Network-dependent tests do not run in CI by default; they may be run
  manually with `pytest -m network`.
- Adding a new public CLI command or public interface without also updating
  fixtures, design docs, and contracts will fail L99 signoff.
