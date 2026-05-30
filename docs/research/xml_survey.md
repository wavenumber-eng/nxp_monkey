# NXP KEX per-part XML survey

Status: dev-only research. Excluded from sdist per ADR-0004.

## Method

Live-fetched **33 representative parts** from the KEX storage API on
the `ksdk2_0` SDK variant (current portfolio-latest, tool version
`26.03`). Round 1 covered 14 parts; round 2 added 19 more modern parts
(i.MX 9, i.MX RT 700, RW612, MCXA156, MCXN947, MCXW727) and legacy
archetypes (Kinetis K, LPC54/55, MK22, MKE02, LPC802, i.MX 8MQ,
i.MX RT 1064/1176).

Round 1 sample:

| Part | Why |
|------|-----|
| `MCXA132` | low-end MCXA (Cortex-M33) |
| `MCXN235` | mid MCXN (dual-core M33) |
| `LPC51U68` | LPC (Cortex-M0+) |
| `MIMXRT1011xxxxx` | i.MX RT crossover (Cortex-M7) |
| `KW45B41Z52xxxA` | wireless KW |
| `RW610` | tri-radio (current) |
| `MC56F80623` | 56800 DSP (no Cortex) |
| `MK02FN128xxx10` | Kinetis classic |
| `MIMX8MD6xxxHZ` | i.MX 8 application processor |
| `MCXC041` | MCXC (Kinetis-derived MCX) |
| `MCXE245` | MCXE |
| `MCXW235` | MCXW wireless |
| `MKE02Z16xxx4` | MKE |
| `MCIMX7U3xxxxx` | older i.MX (older `registers/5.0` schema) |

Round 2 sample (modern + legacy coverage):

| Part | Why |
|------|-----|
| `MIMX9352xxxxM` | i.MX 93 (application processor, A55 + M33) |
| `MIMXRT798S` | i.MX RT 700 flagship (largest crossover MCU) |
| `MIMXRT595S` | i.MX RT 500 (audio crossover) |
| `MIMXRT685S` | i.MX RT 600 (audio + HiFi DSP) |
| `MIMXRT1176xxxxx` | i.MX RT 1170 (dual-core M7+M4 crossover) |
| `MIMXRT1064xxxxA` | i.MX RT 1060 (mid crossover with `dcdx/`) |
| `RW612` | RW6 series (current) |
| `MCXA156` | top-bin MCXA |
| `MCXN947` | top-bin MCXN (current) |
| `MCXW727CxxxA` | newer MCXW wireless (BLE 5.4 / 802.15.4) |
| `MCXW727AxxxA` | MCXW72 sibling SKU |
| `MCXW727DxxxA` | MCXW72 sibling SKU |
| `MCXW716CxxxA` | MCXW71 (prior MCXW gen) |
| `MCXC444` | MCXC larger |
| `MCXE247` | MCXE larger |
| `LPC55S16` | LPC55 mid-bin |
| `LPC55S69` | LPC55 top-bin |
| `LPC54608J512` | LPC54 |
| `LPC802` | LPC8 smallest |
| `MK22FN512xxx12` | Kinetis K22 |
| `MKE16Z64xxx4` | Kinetis KE16 |
| `MIMX8MQ6xxxJZ` | i.MX 8MQ (older app processor) |

All fetched cleanly through `nxp_monkey.fetch(part)`. Cache root:
`%LOCALAPPDATA%\wavenumber\nxp_monkey\Cache\kex\26.03\processors\<PART>\ksdk2_0\`.

**Families that were probed but are NOT in the current KEX portfolio
(at version `26.03`):** `JN5189` (JN51/52 series), `S32K` (S32K1/3),
`MIMX8DXP`, `MIMX6` (legacy i.MX 6). For these, `fetch()` returns
`Unknown processor family`. NXP serves S32 and JN parts from separate
tools / SDKs; they are out of scope for `nxp_monkey` v0.1.

### S32K (automotive) coverage note

The S32K family
(S32K1 / S32K3 / S32K36/37/39 / new S32K5) is intentionally
out-of-scope for v0.1. The MCUXpresso KEX storage backend at
`mcuxpresso.nxp.com/SWTools/...` covers only tool id 1 + 2 (both
MCUXpresso Config Tools); tool ids 3-15 return empty `<response/>`.

S32K data is distributed via a different toolchain:

- **S32 Design Studio** — Eclipse IDE with its own update sites
- **S32 Config Tools** — successor to Processor Expert, separate
  downloader
- **NXP RTD (Real-Time Drivers)** — login-gated downloader
- **AUTOSAR MCAL** — login-gated portals

These have different URL shapes, often require authentication, and
ship a different data shape (MCAL/AUTOSAR config, functional safety
metadata, ASIL classification) than the MCU-class XML tree this
survey covers. A future `nxp_monkey` companion tool — e.g.
`s32_monkey` — could wrap them, but it would be a structurally
separate project, not an extension of v0.1's reader set.

## Headline numbers

| Part | Files | XML | Variants | Registers/XML |
|------|------:|----:|---------:|--------------:|
| MCXA132 | 108 | 86 | 3 | 49 |
| MCXN235 | 191 | 150 | 4 | 76 |
| LPC51U68 | 75 | 60 | 2 | 29 |
| MIMXRT1011xxxxx | 135 | 96 | 2 | 55 |
| KW45B41Z52xxxA | 161 | 136 | 2 | 88 |
| RW610 | 175 | 147 | 3 | 85 |
| MC56F80623 | 80 | 51 | 1 | 19 |
| MK02FN128xxx10 | 102 | 87 | 3 | 39 |
| **MIMX8MD6xxxHZ** | **124** | **20** | **1** | **n/a (app proc)** |
| MCXC041 | 77 | 65 | 2 | 32 |
| MCXE245 | 120 | 96 | 3 | 50 |
| MCXW235 | 129 | 107 | 4 | 65 |
| MKE02Z16xxx4 | 69 | 56 | 3 | 25 |
| MCIMX7U3xxxxx | 118 | 95 | 2 | 64 |
| **Total** | **1670** | **1252** | — | — |

MIMX8MD6 is the obvious outlier — i.MX 8 application processors have
~85% non-XML payload (binaries, scripts, DDR-init blobs).

## Tree shape (MCU class, 29/33 parts)

```
processors/<PART>/<variant>/
  <PART>.xml                          # <processor> root; producer/family/series, enabled tools
  <PART_VARIANT>.xml                  # <part_number>; one per package SKU; lists db_links (manifest)
  <PART_VARIANT>.mex                  # (newer parts) Config Tool save format
  <PART_VARIANT>/                     # variant detail subtree
    part_info.xml                     # NDA flag only (thin)
    module_clocks.xml                 # per-variant clock-module wiring
    peripherals_model_info.xml        # peripheral catalog
    signal_configuration.xml          # pin-mux + signal-routing model (big)
    memory_map.xml                    # (some parts) memory regions
    registers/<PERIPHERAL>.xml        # 19-88 files per part, one per peripheral instance
    resource_tables/*.xml             # DMA, interrupts, ADC channels, clock gates, ...
    security/*.xml + *.json           # (newer parts) trust zone, MPU, IDAU
    *.js                              # peripheral-specific codegen scripts
  clocks/                             # 29/33 parts (absent on i.MX app processors)
    TOP.xml                           # global topology
    POWER_MODES.xml
    <MODULE>.xml                      # SCG, MRCC, SYSCON, CCM, ...
    CLOCKS_DIAGRAM.dsn                # graphical diagram (binary)
  common/
    cores_info.xml                    # CPU core descriptors
    sdk_features.xml                  # FSL_FEATURE_* macros (some parts; not all)
  packages/
    <PACKAGE>.xml                     # one per physical package; GEOMETRY ONLY, no pin list
  scripts/                            # JS codegen scripts (clock_config_c.js, pins_code_gen.js, ...)
  sdk/
    sdk_components.xml                # SDK module catalog (NO xml namespace)
  security/                           # (LPC55S, MCXN235, MCXW235, MIMX93, MIMXRT1176)
                                      # cross-variant security artifacts
  mem_validation/                     # (i.MX RT 10xx, MIMXRT1176, i.MX 8/9) flash boot helpers
  dcdx/                               # Device Configuration Data — SEMC-equipped
                                      # i.MX RT crossovers (RT1020/1060/1170).
                                      # Single `dcdx_model.xml` (~3.5 KB)
                                      # declares DCD header layout +
                                      # allowed boot-time memory ranges.
                                      # Namespace: dcd/2.0
  ddr/                                # (i.MX 8/9 only) DDR controller helpers
  processor.properties                # version=YY.MM.PP (single line, AT LEAST in current data)
  ReleaseNotes.txt                    # (some parts)
```

### Optional top-level directory matrix (round 2)

| Dir | Parts that have it |
|-----|--------------------|
| `clocks/` | every MCU-class part (29/33); absent on i.MX 7/8/9 app procs |
| `security/` | LPC55S16, LPC55S69, MCXN235, MCXW235, MIMX9352xxxxM, MIMXRT1176 |
| `mem_validation/` | MIMXRT1011, MIMXRT1064, MIMXRT1176, MIMX8MD6, MIMX8MQ6, MIMX9352 |
| `dcdx/` | **SEMC-equipped i.MX RT crossovers only** — MIMXRT1021 (RT1020), MIMXRT1064 (RT1060), MIMXRT1176 (RT1170). Absent on MIMXRT1011 (no SDRAM controller) and on the FlexSPI-XIP-first RT 500/600/700 lines. One `dcdx_model.xml` per part. |
| `ddr/` | MIMX8MD6, MIMX8MQ6, MIMX9352 only |

## Application-processor outlier class (i.MX 7/8/9)

Confirmed across `MIMX8MD6xxxHZ`, `MIMX8MQ6xxxJZ`, `MIMX9352xxxxM`,
and `MCIMX7U3xxxxx`:

- No `clocks/` directory (used as the `is_application_processor` flag)
- Have `ddr/` on the i.MX 8/9 generations; older `MCIMX7U3` does not
- `mem_validation/` is very rich (FlexSPI, DDR, DCD configs)
- Have `.so`, `.dll`, `.pkl`, `.incv`, `.h`, `.strings`, `.component`
  payloads alongside XML
- 1–6 variant subdirs (e.g. `MIMX9352xxxxM` ships 4 variants:
  `MIMX9352AVTXM`, `CVVXM`, `DVVXM`, `XVVXM`)

This is application-processor territory and the data model treats it
differently. Conformance/expectations for "details" should not assume
MCU-class layout for these parts. The `PartDetails` flag
`is_application_processor` is derived from `cores_info.xml`: True iff
any core name starts with `Cortex-A`. This is variant-stable —
`cores_info.xml` lives in `common/` and is identical between
`ksdk2_0` and `zephyr3_2`. Classifies all 4 app-proc samples
(i.MX 7U3, 8MD6, 8MQ6, 9352) and none of the MCU samples.

An earlier candidate — "no top-level `clocks/` directory" — was
rejected because Zephyr-variant binders never ship `clocks/` for any
MCU, so the check false-positived on every MCU under `zephyr3_2`.

## `zephyr3_2` vs `ksdk2_0` — what actually differs

Same silicon, different binder. Measured on `MCXA156` (full diff):

| Aspect | `ksdk2_0` | `zephyr3_2` |
|--------|----------:|------------:|
| Total files | 155 | 157 |
| XML files | 127 | 137 |
| Common-path identical content | 73 | 73 |
| Common-path different content | 30 | 30 |
| Files unique to this variant | 52 | 54 |

**Only in `ksdk2_0`** — MCUXpresso Config Tools layer:

- top-level `clocks/` (`TOP.xml`, `POWER_MODES.xml`, per-module
  topology, clock diagram `.dsn`)
- `<VARIANT>/module_clocks.xml`
- `<VARIANT>/resource_tables/*.xml` (clock gates, DMA requests,
  interrupts, ADC channels, FlexIO, …)
- 6 extra `db_link` types in the manifest: `clocks_model`,
  `clocks_scripts`, `module_clocks`, `sdk_features`,
  `cfg_components`, `resource_tables`

**Only in `zephyr3_2`** — extra per-variant register materialisation:

- Per-peripheral register XMLs duplicated under each variant subdir
  (`ksdk2_0` only fully populates one canonical variant and references
  it from the others; `zephyr3_2` materialises the full set per
  variant)

**Content-different in both** (~30 files):

- `<PART>.xml`, `<PART_VARIANT>.xml` — manifests differ only in the
  `db_link` rows (subset vs full)
- `<VARIANT>/signal_configuration.xml` — same pin-mux model, different
  tool-target hints
- `<VARIANT>/*.js` codegen scripts (DMA.js, EIM.js, ERM.js,
  `scripts/pins/pins_code_gen.js`) — Zephyr emits Devicetree
  fragments; KSDK emits SDK C source
- `sdk/sdk_components.xml` — lists Zephyr modules vs SDK modules
- `processor.properties` — variant marker

**Practical implication.** For inspecting silicon facts (registers,
pins, peripherals, packages, cores) `ksdk2_0` is sufficient and
complete — `zephyr3_2` adds no new silicon data, only rearranges
register materialisation. For **generating Zephyr-targeted code**
(DT overlays, Kconfig, pin-mux fragments) you need the `zephyr3_2`
variant's codegen `.js` scripts. Note that `zephyr3_2` itself does
not ship `.dts`/`.dtsi`/`.overlay` files — those live in the upstream
Zephyr NXP HAL, not in KEX; the variant ships the **inputs** the
codegen uses to emit them.

## The spine of variant data is `<PART_VARIANT>.xml`

This is the most useful single file in the tree. It is a **manifest** of
typed `db_link` entries pointing at every other data file for that
variant. Example (`MCXA132VFM.xml`):

```xml
<part:part_number ... id="MCXA132VFM">
  <db_link type="part_info"          link="MCXA132VFM/part_info.xml"         format_version="1.0" .../>
  <db_link type="package"            link="packages/QFN32.xml"               format_version="1.0" .../>
  <db_link type="registers"          link="MCXA132VFM/registers/registers.xml" format_version="6.0" .../>
  <db_link type="peripherals_model"  link="MCXA132VFM/peripherals_model_info.xml" format_version="12.0" .../>
  <db_link type="pins_model"         link="MCXA132VFM/signal_configuration.xml" format_version="12.0" .../>
  <db_link type="cores_info"         link="common/cores_info.xml"            format_version="1.0" .../>
  <db_link type="clocks_model"       link="clocks/TOP.xml"                   format_version="1.2" .../>
  <db_link type="clocks_scripts"     link="scripts/clocks"                   format_version="1.2" .../>
  <db_link type="module_clocks"      link="MCXA132VFM/module_clocks.xml"     format_version="1.2" .../>
  <db_link type="sdk_features"       link="common/sdk_features.xml"          format_version="1.0" .../>
  <db_link type="cfg_components"     link="$COMMON"                          format_version="1.0" .../>
  <db_link type="resource_tables"    link="MCXA132VFM/resource_tables"       format_version="1.0" .../>
</part:part_number>
```

Crucially, the manifest also encodes the **package** for that variant
(`packages/QFN32.xml` here) — so the per-variant package mapping is
data, not inference.

## `<PART>.xml` (processor) summary

The top-level processor file is also small and clean:

```xml
<part:processor ...>
  <basic_facts id="MCXA132" producer="NXP" family="MCX" series="MCX MCXA"
               default_part="MCXA132VLF"/>
  <target_products><product>MCUX</product></target_products>
  <enabled_tools>
    <enabled_tool>Pins</enabled_tool>
    <enabled_tool>Clocks</enabled_tool>
    <enabled_tool>Peripherals</enabled_tool>
  </enabled_tools>
</part:processor>
```

## What `part_info.xml` and `packages/<PKG>.xml` actually contain

Both turn out to be **thinner than expected**:

- `part_info.xml` for current parts is just `<requiresNDA>false</requiresNDA>`.
- `packages/<PKG>.xml` is **package geometry only** (width, height, pin
  count, pin-to-pin spacing). It is the visual layout for the Config
  Tool's package view — NOT a list of pin functions. Pin-function data
  lives in `signal_configuration.xml`.

This is good news for the spine model: those two are negligible to
parse and barely contribute to `PartDetails`.

## XML namespace universe (wider sample, 33 parts, 3,607 XML files)

The wider survey produced **18 distinct XML namespaces** (plus the
universal `xsi`). All version-tagged. No new namespaces showed up on
i.MX RT 700, RW612, or the latest MCXA/N/W parts versus round 1; the
modern parts continue to standardise on `registers/6.0`,
`clocks/1.2`, `pinsModel`, and `part_number/4.0`.

| Namespace | Notes |
|-----------|-------|
| `…/registers/6.0/regsPeripheral.xsd` | current peripheral register schema (dominant) |
| `…/registers/6.0` | wrapper element |
| `…/registers/5.0/regsPeripheral.xsd` | older peripheral schema (MCIMX7U3, some i.MX 8) |
| `…/registers/4.0/regsPeripheral.xsd` | legacy peripheral schema (older Kinetis) |
| `…/registers/3.0/regsPeripheral.xsd` | oldest peripheral schema (round-2 only — pre-MCX) |
| `…/resource/1.0` | resource tables (DMA, interrupts, ADC channels, …) |
| `…/schemas/clocks/1.2` | clocks (current) |
| `…/schemas/clocks/1.1` | older clocks |
| `…/Pins/PinsModel` | `signal_configuration.xml` |
| `…/Pins/PeripheralsModelInfo.xsd` | `peripherals_model_info.xml` |
| `…/part_info/1.0` | `part_info.xml` |
| `…/part_number/4.0` | per-variant manifest |
| `…/package/1.0` | package geometry |
| `…/processor/2.0` | top-level `<PART>.xml` |
| `…/processor/2.0/` | trailing-slash typo on `cores_info.xml` (upstream) |
| `…/features/1.0` | `sdk_features.xml` |
| `…/dcd/2.0` | **i.MX RT only** — Device Configuration Data (`dcdx/`) |
| `…/XSD/resource/1.0` | resource tables |
| (no xmlns) | `sdk_components.xml`, `mem_validation/*` configs |

Schema-version buckets remain clean: `registers/{3,4,5,6}.0`,
`clocks/{1.1,1.2}`, plus the single-version `part_number/4.0`,
`pinsModel/12.0`, `package/1.0`, `features/1.0`, `dcd/2.0`. A reader
selector can dispatch on the namespace URI without further heuristics.

## Schema availability

`xsi:schemaLocation` URLs at `swtools.freescale.net` and `apif.nxp.com`
all fail DNS — **the XSD files are not publicly hosted.** Implication:
schema is inferred from instances. The namespace version tags
(`registers/{4,5,6}.0`, `clocks/{1.1,1.2}`, `part_number/4.0`,
`pinsModel/12.0`, etc.) give us clean schema-version buckets to switch
parsers on.

## Implications for `nxp_monkey` data model

1. **Right grain is package-variant, not bare part.** Pins, peripherals
   and registers all live under `<PART_VARIANT>/`. A `MCXA132` is
   actually three SKUs: `MCXA132VFM` (QFN32), `MCXA132VFT`, `MCXA132VLF`.

2. **The per-variant manifest (`<PART_VARIANT>.xml`) is the right spine.**
   It enumerates every relevant data file, with type tags and format
   versions. A reader of this file gives us a complete map of the
   variant without guessing.

3. **The top-level processor file is a small, durable header.** family,
   series, producer, default_part, enabled tools — perfect for
   `Processor` / `PartDetails.header`.

4. **`cores_info.xml` is the only useful "common" file.** It tells us
   whether a part is single- or multi-core and core types. Tiny.

5. **`part_info.xml` and `packages/*.xml` are nearly empty.** Don't
   over-invest in readers for these.

6. **Register data dominates volume (~54% of XML files).** Lazy-load via
   `PartDetails.open_registers(variant)` rather than eager parse.

7. **App-processor parts (i.MX 7/8/9) need a separate code path.**
   Don't force MCU-class assertions on them. `PartDetails` exposes
   `is_application_processor: bool` and tolerates missing `clocks/`.
   The wider survey confirms this discriminator on all 4 app-proc
   samples (no false positives in 29 MCU samples).

8. **Optional top-level dirs (`security/`, `dcdx/`, `ddr/`,
   `mem_validation/`) are class hints.** A v0.2 `PartDetails` could
   expose `has_security`, `has_dcd`, `has_ddr` booleans cheaply from
   directory presence — no XML parse required.

9. **i.MX RT 10xx `dcdx/` is the only new namespace surfaced in the
   wider survey** (`…/dcd/2.0`). Modern parts (i.MX RT 700, RW612,
   MCXA156, MCXN947, MCXW71/72) do not introduce new schema versions —
   they reuse `registers/6.0`, `clocks/1.2`, `pinsModel`. This is
   evidence that v0.1's reader set is stable across the modern
   portfolio.

10. **MCXW72 (`MCXW727A/C/DxxxA`) is a single-Core-0 view of
    dual-core silicon.** `cores_info.xml` lists only
    `Cortex-M33 (Core #0)` — the dedicated radio NBU (BLE/802.15.4)
    coprocessor is not exposed as a developer-accessible core.
    Security data is present but lives in
    `<VARIANT>/security/` (`idau.xml`, `mpu.xml`, `msw.xml`,
    `trust_model.xml`, `trust_zone.{json,yaml}`), not in a top-level
    `security/` directory like MCXW235 / MIMXRT1176. v0.1 readers
    handle this transparently — `details(...).cores` returns a
    1-element tuple, `is_application_processor` is `False`, and
    `link_by_type("trust_model")` resolves to the variant-local file.

## v0.1.1 plan

**Add to the public surface:**

- `Processor` dataclass — from `<PART>.xml`
- `DbLink` dataclass — `(type, link, format_version, description)`
- `CpuCore` dataclass — from `cores_info.xml`
- `PackageVariant` dataclass — from `<PART_VARIANT>.xml` (id, package,
  db_links)
- `PartDetails` dataclass — top-level container:
  `(part, header: Processor, cores: tuple[CpuCore,...], variants: tuple[PackageVariant,...], properties: dict[str,str], root: Path)`
- `details(part) -> PartDetails` library function (fetches if needed)
- `nxp-monkey details <PART>` CLI command (and `nxpm details ...`)

**Defer:**

- `signal_configuration.xml` parser (pin-mux, large) → v0.1.2
- `registers/*.xml` parser (one schema, but 50-90 files per part) → v0.1.2
- `clocks/TOP.xml` parser → v0.1.2

This lets agents discover the **structure** of a part now and load the
heavy content lazily later.
