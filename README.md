```
                    ██████████
                  ██▓▓▓▓▓▓▓▓▓▓██
                ██▓▓░░░░░░▓▓▓▓░░██
              ████░░      ░░░░    ██
            ██░░▓▓░░  ████░░░░██  ██
            ██░░▓▓░░  ████░░░░██  ██
              ██▓▓░░  ░░░░░░░░░░██
              ██▓▓▓▓▓▓▓▓▓▓░░▓▓░░▓▓██
    ████        ██▓▓▓▓▓▓▓▓░░░░░░▓▓██
  ██▓▓▓▓██    ██▓▓████████▓▓▓▓▓▓██
██▓▓████      ██▓▓▓▓▓▓▓▓▓▓████████
██▓▓██      ██▓▓▓▓██▓▓██▓▓▓▓▓▓▓▓██
██▓▓██    ████▓▓▓▓██▓▓▓▓██████▓▓██
██▓▓██████▓▓██▓▓▓▓██▓▓▓▓▓▓██▓▓▓▓██
  ██▓▓▓▓▓▓████░░░░██░░░░░░██░░░░██
    ██████    ████████████  ████
```

# nxp_monkey

Cross-platform CLI and Python library for fetching, indexing, and searching
NXP MCUXpresso Config Tools chip data, with both **XML** (raw NXP binders)
and **JSON** (parsed spine + lossless XML->JSON mirror) output by default.

`nxp_monkey` talks to NXP's public KEX storage API (the same one the
MCUXpresso Config Tools Data Manager uses) and exposes the per-processor
data tree — signal configurations, register variants, packages, clocks,
resource tables — as a clean local cache that humans, scripts, and LLM
agents can all consume.

## Install

```powershell
uv tool install nxp-monkey
uv tool update-shell
nxp-monkey --version
nxp-monkey version
# Short alias is also installed:
nxpm --version
nxpm version
```

## CLI quick start (MCXA156)

```powershell
# What tool versions does NXP publish?
nxp-monkey versions

# What processor families are available?
nxp-monkey families

# Search by part / family root (partial + fuzzy)
nxp-monkey search MCXA
nxp-monkey search mcxa --fuzzy

# Build a local index of all available parts and variants
nxp-monkey index build
nxp-monkey index show MCXA156

# Fetch one part. Downloads every SDK variant NXP publishes,
# mirrors the XML, and writes the JSON views alongside.
nxp-monkey fetch MCXA156

# Print the parsed chip-data spine (header, cores, package variants)
nxp-monkey details MCXA156

# Print the per-part roadmap (where things live + inferred schema)
nxp-monkey roadmap MCXA156

# Manage the cache
nxp-monkey cache path
nxp-monkey cache size
nxp-monkey cache clear
```

## What you get from `fetch MCXA156`

Output is split by media type so an agent can pick either side:

```
MCXA156/
  xml/                              # raw NXP binders, one subdir per SDK variant
    ksdk2_0/...                     #  - canonical silicon data (registers, pins, clocks)
    zephyr3_2/...                   #  - Zephyr DT codegen scripts
    i_mx_2_0/...                    #  - i.MX Linux binder (when published)
  json/
    MCXA156.json                    # PartDetails spine: header, cores, package SKUs, db_links
    MCXA156.roadmap.json            # agent guide + inferred schema + folder layout
    ksdk2_0/...                     # full XML->JSON mirror (one .json per .xml)
      MCXA156VFT/registers/ADC1.json
      MCXA156VFT/signal_configuration.json
      packages/QFN48.json
      ...
```

For MCXA156 this is ~40 MB of XML plus a 1:1 ~71 MB JSON mirror across
127 files (per variant). Progress is shown live on stderr; stdout stays a
clean list of paths so it pipes well.

## JSON vs XML

| You want... | Read |
|---|---|
| Top-level chip facts (family, cores, packages, SKU list) | `json/<PART>.json` |
| A guide to where things live + observed XML namespaces | `json/<PART>.roadmap.json` |
| One peripheral's registers / bit fields | `json/<variant>/<SKU>/registers/<PERIPH>.json` |
| Pin mux for a specific SKU | `json/<variant>/<SKU>/signal_configuration.json` |
| The bit-identical NXP binder (codegen scripts, exact whitespace) | `xml/<variant>/...` |

The XML->JSON conversion is lossless for data: namespace-stripped tags,
`@attr` keys for attributes, `#text` for mixed content, repeated children
as lists, root `@_xmlns` preserves the source namespace declarations.
Skip the mirror with `--no-json-mirror`, or narrow it with
`--json-mirror-only '**/registers/**'` (repeatable glob).

The global `--json` flag switches stdout to print the JSON output path
(for tool / agent integration); JSON files are always written by
`fetch`.

## Library quick start

```python
import nxp_monkey

# Discovery
versions = nxp_monkey.list_versions()
families = nxp_monkey.list_families()

# Search
hits = nxp_monkey.search("MCXA", fuzzy=False)

# Index
nxp_monkey.build_index()
info = nxp_monkey.get_part("MCXA156")     # -> PartInfo dataclass

# Fetch + parse
path = nxp_monkey.fetch("MCXA156")        # -> pathlib.Path to unpacked tree
details = nxp_monkey.details("MCXA156")   # -> PartDetails (header, cores, variants)
roadmap = nxp_monkey.build_roadmap(path)  # -> dict (guide, key_files, xml_namespaces, ...)

# Cache control
nxp_monkey.cache_path()
nxp_monkey.cache_size()
nxp_monkey.cache_clear()
```

## Documentation

- `docs/setup.html` - setup, release, and artifact policy.
- `docs/architecture.html` - package layers and ownership boundaries.
- `docs/adrs/` — architecture decision records.
- `docs/design/cli/` — one HTML doc per CLI command.
- `docs/design/api/` — one HTML doc per public library interface.
- `docs/contracts/` — JSON manifests + schemas (command + interface).
- `docs/research/xml_survey.md` — narrative survey of the KEX XML schema universe.

## Testing

Rack is the primary local signoff gate:

```powershell
uv sync --all-extras
uv run rack run --all
uv run python -m build
uv run twine check dist/*
uv run python tests/support_scripts/install_test.py
```

## License

MIT. See `LICENSE`.

`nxp_monkey` fetches public NXP data on behalf of the user. NXP MCUXpresso
Config Tools content is NXP material and is governed by NXP terms.
