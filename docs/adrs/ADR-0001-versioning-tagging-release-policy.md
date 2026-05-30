# ADR-0001: Versioning, Tagging, And Release Policy

Status: accepted
Date: 2026-05-29

## Context

`nxp_monkey` is a new public Python package that wraps NXP's public MCUXpresso
Config Tools KEX storage API. It is a sibling of `altium_cruncher` and
`easyeda_monkey` in the Wavenumber LLC public tool family and will be published
to PyPI and accept public PRs. It needs a clear source-of-truth release policy
and a repeatable automation path that matches the sibling packages.

## Decision

`nxp_monkey` uses date-based PEP 440 versions:

- normal release: `YYYY.M.D`
- supplemental build release: `YYYY.M.D.N`

The Git tag for a release is `v<version>`, for example `v2026.5.29`.

The release source is the standalone public repository
`github.com/wavenumber-eng/nxp_monkey`. There is no generated public-export flow.

The normal publishing path is GitHub Actions with PyPI Trusted Publishing / OIDC:

1. merge to protected `main` after CI passes;
2. create an annotated `v<version>` tag or GitHub Release from the release
   commit;
3. release workflow verifies package version metadata matches the tag;
4. release workflow verifies release notes mention the version;
5. release workflow runs tests, signoff, package build, install tests, and
   `twine check`;
6. release workflow publishes wheel and sdist to PyPI.

Release artifacts may include public reference docs such as ADRs, design docs,
contracts, examples, and tests. Developer planning and research notes under
`docs/plans/` and `docs/research/` are not part of the public release artifacts
and must be excluded from source distributions.

Local Twine upload is reserved for emergency fallback.

Public command names, command-line flags, stable JSON outputs, on-disk cache
layout, and public Python import surface are compatibility surfaces. Breaking
changes require an ADR or release note that states the migration path.

## Consequences

- CI and `L99_signoff` must be release gates, not advisory checks.
- Release artifacts are traceable to a public source commit and annotated tag.
- Direct PRs can be accepted, but only after protected-branch CI/signoff passes.
- The same policy is shared with `altium_cruncher` and `easyeda_monkey`;
  changes here should consider impact across the tool family.
