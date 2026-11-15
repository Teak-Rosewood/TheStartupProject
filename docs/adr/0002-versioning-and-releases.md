# ADR 0002: One version for the whole repository

- Status: accepted
- Date: 2026-11-15

## Context
Products built on the kit depend on its packages from Git, pinned to a tag
(`tsp-cms @ git+https://github.com/Teak-Rosewood/TheStartupProject@v0.2.0#subdirectory=python/packages/cms`).
Until now there were no tags, and all 29 packages sat at `0.1.0` whatever changed. The packages
depend on each other closely (`tsp-engage` needs `tsp-cms` and `tsp-pipeline`, which need
`tsp-jobs`, and so on), so versioning them separately would mean a compatibility matrix nobody
maintains.

## Decision
- One SemVer version for every Python package and app, kept in the root `VERSION` file and
  copied into each `pyproject.toml` by `scripts/version.py set X.Y.Z` (CI runs
  `scripts/version.py check`). The JS packages are private apps and keep their own versions.
- A release is a Git tag `vX.Y.Z` on `master`. The `release` workflow runs the full gate, builds a
  wheel for every package and creates a GitHub release from the matching `CHANGELOG.md` section.
  Nothing is published to PyPI yet.
- While the version is below `1.0`, a minor bump may break public APIs, but every break is listed
  under **Breaking** in the CHANGELOG with what to change. Patch releases never break.
- A released migration is never edited; fixes are new migrations.

## Consequences
- Depending on the kit is one pin for all packages, and upgrading is one bump plus reading the
  CHANGELOG's Breaking list.
- A change to one package releases all of them. That is cheap: releases are tags, not uploads.
- Revisit when an outside user needs one package on its own, or at 1.0.
