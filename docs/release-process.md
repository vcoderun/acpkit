# Release Process

ACP Kit publishes five synchronized packages from one tag. A release is valid
only when every package reports the same PEP 440 version.

## Prepare

1. Update every `_version.py` file.
2. Add the matching heading to `CHANGELOG.md`.
3. Update compatibility bounds, maintained examples, package READMEs, skills,
   and release notes when behavior changed.
4. Regenerate `uv.lock`.

## Validate

Run the complete release gate:

```bash
make release RELEASE_TAG=v1.0.0_2026_07_04
```

The gate:

- runs tests, lint, formatting checks, type checks, documentation builds, and
  compatibility matrices
- requires 100% adapter line and branch coverage
- validates synchronized versions and the release tag
- builds every wheel and source distribution into `dist/`
- inspects package names, versions, and root dependency metadata
- installs `acpkit[all]` from the built wheels in a clean environment
- verifies imports, versions, and both console entry points

## Publish

Push a matching version tag. An optional calendar-date suffix can distinguish the
repository release event without changing the package version:

```bash
git tag -s v1.0.0_2026_07_04 -m "ACP Kit 1.0.0"
git push origin v1.0.0_2026_07_04
```

`v1.0.0_YYYY_MM_DD` is the canonical dated release tag. Both `v1.0.0` and the
legacy `v1.0.0_YYYY-MM-DD` form also validate against package version
`1.0.0`. The suffix does not permit republishing an existing PyPI version.

GitHub Actions repeats the release gate, uploads the artifacts for inspection,
and publishes through PyPI Trusted Publishing. Never publish from a dirty
worktree or bypass a failed artifact smoke test.

## After Publishing

1. Verify all five package pages report the synchronized version.
2. Install `acpkit[all]` from PyPI in a clean environment.
3. Create the GitHub release from `CHANGELOG.md`.
4. Publish the documentation build.
