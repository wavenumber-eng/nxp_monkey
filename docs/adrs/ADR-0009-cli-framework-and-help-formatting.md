# ADR-0009: CLI Framework And Help Formatting

Status: accepted
Date: 2026-05-29

## Context

The CLI shape of `nxp_monkey` is part of its public surface. The CLI is
consumed by humans and by LLM/agent workflows that walk `--help` output and
the command manifest to plan invocations. Several CLI frameworks are
available (argparse, click, typer); the choice affects both human ergonomics
and machine introspection.

## Decision

### Framework: argparse

`nxp_monkey` uses `argparse` from the standard library. Reasons:

- agent introspection: the argparse parser tree is walkable via
  `parser._actions` and `parser._subparsers`, so the command manifest in
  `docs/contracts/command_manifest.v0.json` can be generated and validated
  from literal code. Decorator-based frameworks (click, typer) hide the CLI
  shape behind framework internals;
- zero non-stdlib dependency for the parser itself (per ADR-0002 dependency
  discipline);
- consistency with `altium_cruncher` and `easyeda_monkey`, which use
  argparse;
- library-first design (ADR-0002) is easier to enforce with argparse
  because the CLI module never gets decorated with framework metadata;
  it stays a thin wrapper.

### Colored help formatting: rich-argparse

`nxp_monkey` uses `rich-argparse` solely as the help formatter:

```python
from rich_argparse import RichHelpFormatter
parser = argparse.ArgumentParser(formatter_class=RichHelpFormatter, ...)
```

This is a formatter-only swap — the argparse parser tree itself is
unchanged, so manifest generation and L99 introspection work identically.
`rich-argparse` provides:

- color by syntactic role (usage line, metavars, option flags, choices,
  defaults, section headings);
- automatic respect of `NO_COLOR`, `FORCE_COLOR`, and `isatty()`;
- markdown-style emphasis in `description=` and `epilog=`.

### Help conventions

Every subparser uses:

- `formatter_class=RichHelpFormatter`;
- a one-paragraph `description=` (what the command does, when to use it);
- a 1-3 example `epilog=`;
- `argparse.add_argument_group` for sectioned `--help`;
- `metavar=` on every positional and option;
- `choices=` on enum-like flags;
- custom `type=` callables for paths, comma-lists, version strings.

The root parser provides:

- `nxp-monkey --version` wired to `nxp_monkey._version.__version__`;
- `nxp-monkey help <cmd>` alias to `nxp-monkey <cmd> --help`;
- top-level epilog pointing at `docs/design/cli/` for the full HTML design
  docs.

### Color palette

The palette is tuned to be high-contrast and not rainbow, matching the
monochrome aesthetic of the HTML design docs. Configured at root-parser
creation time and inherited by all subparsers.

## Consequences

- The manifest under `docs/contracts/command_manifest.v0.json` is generated
  from and validated against the live argparse tree by `L99_signoff`.
- Switching CLI framework is an ADR-level change.
- `rich-argparse` is a formatter dep, not a parser dep; if it ever becomes
  problematic it can be swapped for a hand-rolled `HelpFormatter` subclass
  without touching command modules.
