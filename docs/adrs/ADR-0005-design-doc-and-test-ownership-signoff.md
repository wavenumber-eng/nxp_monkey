# ADR-0005: Design Documentation And Test Ownership Signoff

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` is a public CLI + library package. Public commands, config
formats, JSON outputs, dataclasses, and major interfaces need durable design
documentation so users, LLM agents, and future maintainers can understand the
intended behavior before changing it.

## Decision

Design documentation is part of release signoff.

### Per-command design docs

Every public CLI command in `docs/contracts/command_manifest.v0.json` must
have a matching HTML design document:

- path: `docs/design/cli/<command-name>.html`;
- filename matches the command name exactly;
- document includes `data-command="<command-name>"` on a top-level section;
- document includes `usage`, `arguments`, `output`, and `tests` sections;
- document declares `data-config-contract="none"` or names the
  machine-readable config/output contract it uses.

### Per-interface design docs

Every public dataclass and every public function/class listed in
`docs/contracts/interface_design_manifest.v0.json` must have a
machine-readable design section in `docs/design/api/*.html`:

- section attribute: `data-interface="<symbol>"`;
- section attributes for test file and test target;
- rationale, purpose, test requirements, and working definition.

### Docstrings

Every public function and class must have a non-empty PEP 257 docstring.
`L99_signoff` enforces this.

### Master entry point

`docs/design/index.html` is the master human and machine entry point.
`docs/design/styles.css` is the shared style file. Design HTML should remain
simple, monochrome, monospace, and easy to parse with text or HTML tooling.

### Contracts

Commands with config files or stable machine-readable output need contracts
under `docs/contracts/` and conformance tests before the command is
release-ready. Generated configs and outputs remain strict JSON by default
because that is easy to parse and validate.

## Consequences

`L99_signoff` fails when command design docs, interface design docs,
docstrings, or test ownership links are missing.

Adding a new public CLI command or public interface requires updating
the corresponding contract manifest, design doc, and tests in the same
change.
