# Contributing to nxp_monkey

Thank you for considering a contribution.

## Project posture

`nxp_monkey` is a public CLI + library for fetching NXP MCUXpresso Config
Tools chip data. The governance docs live in `docs/adrs/`. Before opening a
non-trivial PR, please read at minimum:

- `docs/adrs/ADR-0001-versioning-tagging-release-policy.md`
- `docs/adrs/ADR-0002-cli-install-library-import-dependency-discipline.md`
- `docs/adrs/ADR-0005-design-doc-and-test-ownership-signoff.md`

## Development setup

```powershell
git clone https://github.com/wavenumber-eng/nxp_monkey C:\eli\nxp_monkey
cd C:\eli\nxp_monkey
uv sync --all-extras
uv run rack run --all
```

For local CLI testing as an installed tool:

```powershell
uv tool install --force --editable C:\eli\nxp_monkey
nxp-monkey --version
```

## Tests

Tests are stratified per ADR-0003:

- `tests/L0_public_cli/` — fast CLI smoke tests.
- `tests/L3_public_workflows/` — library workflow tests against offline
  fixtures.
- `tests/L99_signoff/` — structural / conformance tests.

The default Rack invocation runs L0 + L3 + L99 and is the gate for commits and
PRs. Network tests are opt-in:

```powershell
uv run pytest -m network
```

## What signoff checks

`L99_signoff` will fail your PR if:

- a new public CLI command does not have a matching HTML design doc under
  `docs/design/cli/` with a `data-command=` attribute;
- a new public interface (function, class, dataclass) does not have a
  matching HTML design doc under `docs/design/api/` with a `data-interface=`
  attribute;
- a public function or class is missing a PEP 257 docstring;
- the command manifest does not match the live argparse tree;
- a JSON contract example does not validate against its schema;
- a CLI command module imports business logic from outside the library;
- a new dependency is added without justification in ADR-0002.

## Adding a new public CLI command

1. Add `src/py/nxp_monkey/nxp_monkey_cmd_<name>.py` (thin wrapper around a
   library function).
2. Register it in `src/py/nxp_monkey/_cli.py`.
3. Add or update the library function it wraps.
4. Add `docs/design/cli/<name>.html` with `data-command="<name>"`.
5. Add or update `docs/contracts/command_manifest.v0.json`.
6. Add tests under `tests/L0_public_cli/` and `tests/L3_public_workflows/`.

## Adding a new public library interface

1. Add or change code under `src/py/nxp_monkey/`.
2. Re-export from `src/py/nxp_monkey/__init__.py` if it is part of the
   public API.
3. Add `docs/design/api/<group>.html` (or extend an existing file) with a
   `data-interface="<symbol>"` section.
4. Add or update `docs/contracts/interface_design_manifest.v0.json`.
5. Add tests under `tests/L3_public_workflows/`.

## Commit / PR style

- Small, focused commits.
- Reference an ADR if your change touches governed surfaces.
- One PR per logical change. CI must be green.
