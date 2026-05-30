# ADR-0008: SDK Variant Policy

Status: accepted
Date: 2026-05-29

## Context

NXP's KEX storage API exposes per-family data under one or more "SDK variant"
subdirectories:

- `ksdk2_0` — MCUXpresso SDK target (C drivers, `pin_mux.c/.h`,
  `clock_config.c/.h`, peripherals). Most complete and most consistently
  populated for Cortex-M parts.
- `zephyr3_2` — Zephyr RTOS target (devicetree fragments, pinctrl nodes,
  Kconfig). Wraps the same silicon facts with Zephyr-native output.
- `i_mx_2_0` — i.MX application-processor target (Cortex-A, Linux/U-Boot
  context). Different scope and pattern from the MCU variants.

All variants describe the same silicon: same `signal_configuration.xml`, same
package pinouts, same register data, same resource tables. The differences
are entirely in the generated output binders.

## Decision

### Default variant

`ksdk2_0` is the canonical variant for silicon-data fetches. Fetch and
indexing commands default to `--variant ksdk2_0` when no variant is
specified.

This default reflects two observations:

- `ksdk2_0` is the most consistently populated across families and tool
  versions;
- the silicon facts we care about (pin alts, registers, resource tables,
  packages) are present and structured identically across variants, so
  choosing one for the default index does not lose information.

### Exposed variants

The full set `("ksdk2_0", "zephyr3_2", "i_mx_2_0")` is exposed through the
`--variant` flag on every fetch command and through the library API. Users
who need Zephyr-formatted output (DTS, Kconfig) or i.MX output pass
`--variant zephyr3_2` or `--variant i_mx_2_0`.

### Indexing

The index records, per part and per family, which variants are available
upstream. `nxp-monkey search` and `nxp-monkey index show` surface this so a
caller can see "MCXA156 has ksdk2_0 and zephyr3_2 available; i_mx_2_0 is not
published for this family."

### Multi-variant fetch

`nxp-monkey fetch --variant all` fetches every published variant for the
selected part(s). Default behavior remains single-variant for minimal
download cost.

## Consequences

- A caller who simply asks for `nxp_monkey.fetch("MCXA156")` gets the
  MCUXpresso SDK target, which is the right answer for the common case of
  pulling silicon data for analysis.
- Zephyr and i.MX users get first-class CLI and library access through the
  `variant` parameter; the package does not hide non-default variants.
- The index keeps variant availability as a first-class fact so downstream
  tools and agents can plan fetches without trial-and-error.
