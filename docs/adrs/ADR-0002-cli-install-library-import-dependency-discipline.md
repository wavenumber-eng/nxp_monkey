# ADR-0002: CLI Install, Library Import, And Dependency Discipline

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` is both a public command-line application *and* a public Python
library. It should be easy for users to install as a CLI, easy to import from
other tools (including LLM/agent workflows) for programmatic data access, easy
for WN workspace setup to pin, and predictable for CI to test.

Application packages may carry more dependencies than core libraries, but every
dependency still creates install, packaging, CI, and support cost. NXP's KEX
API is public HTTP and returns XML and ZIP payloads; the standard library
handles all transport.

## Decision

### CLI install path

The public CLI install path is:

```powershell
uv tool install nxp-monkey
uv tool update-shell
nxp-monkey --version
```

WN workspace setup/update should install pinned released tool versions with
`uv tool install --force`, then test the generated executable.

Local source development may replace a released tool with an editable checkout:

```powershell
uv tool install --force --editable C:\path\to\nxp_monkey
```

### Library import surface

`nxp_monkey` is library-first. Everything the CLI does must be reachable
through `import nxp_monkey`. The CLI modules are thin wrappers that:

- parse command-line arguments;
- call a library function;
- format the return value for human display.

The top-level `_cli.py` module is an orchestrator. It owns global options,
root parser setup, command registration, and dispatch. Public subcommands own
command-specific parser setup and behavior in `nxp_monkey_cmd_<name>.py`
modules.

The public Python surface re-exported from `nxp_monkey/__init__.py` is a
compatibility surface and follows the breakage rules in ADR-0001.

### Dependency rules

Every new dependency must explain:

- why the dependency is needed;
- why the standard library or existing project dependencies are not sufficient;
- expected install/package impact;
- license compatibility;
- whether the dependency is required, optional, or test-only.

Initial dependencies (v0.1):

| dependency | role | justification |
|---|---|---|
| `platformdirs` | required | Cross-platform user cache directory (Windows `%LOCALAPPDATA%`, macOS `~/Library/Caches`, Linux `XDG_CACHE_HOME`). Stdlib has no equivalent. MIT licensed. |
| `rich-argparse` | required | argparse formatter only; preserves stdlib parser tree for manifest introspection; correct `NO_COLOR` / TTY handling for free. MIT licensed. |
| `pytest` | test-only | Test runner. |
| `pytest-json-report` | test-only | Rack consumes pytest JSON reports for stratum summaries. |
| `jsonschema` | test-only | Contract conformance tests in `L99_signoff`. |
| `pyright` | test-only | Type checking. |
| `ruff` | test-only | Lint + format. |
| `build`, `twine` | test-only | Package build verification. |
| `wn-rack` | test-only | Wavenumber stratum-based signoff runner for local and CI release gates. |

### License

`nxp_monkey` is licensed MIT. The package does not statically link or vendor
any AGPL or GPL code. NXP MCUXpresso Config Tools data fetched from the public
API is NXP material and is governed by NXP terms; `nxp_monkey` is a transport
and indexing tool only.

## Consequences

`pipx` can remain a possible user fallback, but it is not the primary install
path documented for these tools.

Reviewers can reject command or dependency additions that lack justification,
even when tests pass.

The CLI layer must not grow business logic. New behavior lands in library
modules (or new library modules), and CLI modules call into them. L99 signoff
will check that `nxp_monkey_cmd_*.py` modules do not import from outside
`nxp_monkey` other than `argparse` and standard library.
