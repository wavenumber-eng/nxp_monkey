# Changelog

All notable changes to `nxp_monkey` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and
this project adheres to date-based PEP 440 versions per ADR-0001
(`YYYY.M.D[.N]`).

## [2026.5.29] - 2026-05-29

### Added

- Initial public release.
- KEX storage API client (`nxp_monkey.KexClient`) with cached version
  resolution.
- Public library API: `list_versions`, `list_families`, `search`,
  `build_index`, `get_part`, `fetch`, `fetch_all`, `cache_path`,
  `cache_size`, `cache_clear`.
- CLI: `nxp-monkey {versions,families,search,index,fetch,details,cache}`
  with colored help formatting. Short alias `nxpm` is installed alongside.
- `fetch` defaults to grabbing every SDK variant NXP publishes for a
  part (`ksdk2_0` + `zephyr3_2` + `i_mx_2_0`); unpublished variants
  are silently skipped. Use `--variant V` to restrict to a single
  variant. Theme: pull everything the upstream offers, agents sort it
  out. The Zephyr variant ships pinctrl-DT codegen scripts
  (`zephyr_pins_print_code.js`, `zephyr_defines_objects.js`) that
  `ksdk2_0` does not.
- `fetch --output DIR` mirrors each fetched
  `processors/<PART>/<VARIANT>/` tree to `DIR/<PART>/<VARIANT>/`. When
  `--output` is omitted, mirroring happens to the current working
  directory. The cache is always populated either way.
- `fetch` shows a rich progress bar (per part/variant: transferred,
  rate, elapsed) on stderr while ZIPs are streamed from NXP. Stdout
  remains a clean list of one mirrored path per line so it stays
  pipeline-friendly.
- New library hook: ``fetch(..., progress_callback=...)`` and
  ``KexClient.download_directory_zip(..., progress_callback=...)``,
  invoked as ``progress_callback(bytes_so_far, total_or_None)``.
  ``fetch_all(..., progress_factory=...)`` builds a callback per
  (part, variant). The library is UI-agnostic — the CLI owns rich.
- Cache directory no longer inserts a vendor parent on Windows. Path is
  now `%LOCALAPPDATA%\nxp_monkey\Cache` (and unchanged on macOS/Linux).
- `details(part)` + `details_from_cache(part, ...)` library functions and
  `nxp-monkey details PART` CLI command. Returns a structured
  `PartDetails` (processor header, CPU cores, per-package variants with
  typed db_link manifests, properties, application-processor flag).
- New dataclasses: `Processor`, `DbLink`, `CpuCore`, `PackageVariant`,
  `PartDetails`. Mirror the small spine XML files described in
  `docs/research/xml_survey.md`.
- Cross-platform cache via `platformdirs` (ADR-0006).
- SQLite FTS5 index over parts, families, and SDK variants.
- Offline-fixture test stratum (`L0_public_cli`, `L3_public_workflows`,
  `L99_signoff`).
- Design documentation for every CLI command and every public interface.
- Initial ADR set: ADR-0001 through ADR-0009.
