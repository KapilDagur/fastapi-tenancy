# ADR 0001 - Commit uv.lock

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** fastapi-tenancy maintainers
- **Supersedes:** the previous `.gitignore` entry that excluded `uv.lock`
- **Related:** `.devcontainer/post-create.sh`, `.github/workflows/ci.yml`, `Makefile`

## Context

`fastapi-tenancy` is a **library**, not an application. The prevailing convention is
that applications commit their lockfile and libraries do not, on the grounds that a
library must not dictate transitive versions to the projects that depend on it. The
repository followed that convention: `.gitignore` carried an explicit `uv.lock` entry
with the comment "not committed for libraries".

Three forces pushed against keeping it:

1. **The devcontainer became the canonical environment.** `.devcontainer/Dockerfile`
   pins the base image by digest, `uv` by digest, and the ODBC packages by exact apt
   version. Every external input is pinned except the Python dependency set, which was
   re-resolved on every container creation. That is the one remaining source of
   "works on my machine".

2. **The test matrix is unusually sensitive to dependency drift.** The suite exercises
   four database dialects through four async drivers (`asyncpg`, `aiosqlite`,
   `aiomysql`, `aioodbc`) plus SQLAlchemy 2.0 event internals, and the isolation
   providers depend on behaviour that is not part of SQLAlchemy's public contract
   (for example `Session.after_begin` semantics and `ForeignKeyConstraint`
   `_column_tokens`). A silent minor bump can change behaviour without changing any
   version range in `pyproject.toml`.

3. **A lockfile does not, in fact, constrain consumers.** `uv.lock` is used only when
   `uv` operates on *this* project. When `fastapi-tenancy` is installed as a
   dependency, resolvers read the wheel metadata generated from `pyproject.toml` and
   ignore the lockfile entirely. The concern that motivates the library convention
   does not apply to the artefact itself; it applies to the dependency *ranges*, which
   stay broad and unchanged.

The declared ranges in `pyproject.toml` remain the public compatibility contract.
`uv.lock` is a development and CI artefact.

## Options considered

### Option A - Keep ignoring `uv.lock` (status quo)

Follows the library convention with no further explanation required.

- Every devcontainer creation and every CI job re-resolves the dependency graph.
- A transitive release between two runs can turn a green suite red with no change
  authored by anyone in the project, and the failure is not reproducible from the
  repository state alone.
- `uv sync --locked` in `post-create.sh` is impossible, so the container has no way to
  assert that it got the intended environment.

### Option B - Commit `uv.lock` (chosen)

- `post-create.sh` can run `uv sync --all-extras --locked`, which fails loudly when the
  lock and `pyproject.toml` disagree instead of silently resolving something else.
- Dependency bumps become reviewable diffs with an author and a date.
- A red build can be reproduced exactly from a commit hash.
- Costs: lockfile churn in pull requests, and contributors must re-lock after editing
  `pyproject.toml`.

### Option C - Commit a separate dev-only constraints file

Lock the development toolchain (pytest, ruff, mypy, drivers) while leaving runtime
dependencies unlocked.

- Rejected. It reintroduces a second dependency manifest to keep in step with
  `pyproject.toml`, and `uv` has no first-class support for it. The runtime
  dependencies (SQLAlchemy in particular) are precisely the ones whose drift breaks
  this project, so excluding them defeats the purpose.

## Decision

Commit `uv.lock`.

The library convention is a heuristic against constraining consumers. It does not
apply here, because the lockfile has no effect on consumers. Reproducibility of the
devcontainer and CI is a concrete, recurring benefit; the cost is lockfile churn in
diffs, which is acceptable and reviewable.

`pyproject.toml` ranges stay deliberately broad. They, not the lock, define what a
consumer may install.

## Consequences

### Positive

- `uv sync --locked` in `post-create.sh` guarantees the container matches the
  repository, and fails loudly rather than drifting.
- CI and local runs resolve to the same versions.
- Dependency changes are explicit, attributable commits.
- A failing build is reproducible from its commit.

### Negative

- `uv.lock` is roughly 2 100 lines. It appears in diffs whenever dependencies change
  and should be reviewed for the version delta, not read line by line.
- Editing `pyproject.toml` without running `uv lock` produces a stale lock. The
  `--locked` flag catches this in the container; see the follow-up below for CI.
- Automated dependency bots will open pull requests against the lock. Batch them.

### Neutral

- No effect on published wheels or sdists. Hatchling builds from `pyproject.toml`;
  `uv.lock` is not included in the distribution and has no bearing on installs.
- No effect on the supported Python range. The lock is resolved for the environment
  that produced it; `requires-python = ">=3.11"` continues to govern.

## Follow-ups

1. ~~**CI does not currently enforce the lock.**~~ **Resolved** (`e883eae`). Every job
   in `.github/workflows/ci.yml` now runs `uv sync --all-extras --locked`, so a
   dependency set that differs from the committed lock fails the build.

2. **`post-create.sh` falls back on a stale lock.** When `uv sync --locked` fails, the
   script warns and re-runs `uv sync --all-extras`, which rewrites the lock and
   continues. That is a deliberate developer-experience trade-off, but it means a
   stale lock does not block container creation. Revisit if drift becomes common.

3. ~~**`make dev` should use the lock.**~~ **Resolved.** The `dev` target now runs
   `uv sync --all-extras --locked`, so the fastest path to a local environment is
   also the one that honours this decision.

## Notes

This ADR is referenced from the repository `.gitignore`. The `docs/contributing/adr/`
directory is currently local-only (see the `.gitignore` inside it), so that reference
resolves only on machines that have this file. Either publish the directory or inline
the rationale into the `.gitignore` comment before other contributors rely on it.
