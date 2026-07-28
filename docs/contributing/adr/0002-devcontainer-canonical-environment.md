# ADR 0002 - Devcontainer as the canonical development environment

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** fastapi-tenancy maintainers
- **Implemented by:** commit `14975e5`
- **Related:** [0001](0001-commit-uv-lock.md) (commit uv.lock), [0003](0003-testcontainers-replaces-docker-compose.md) (Testcontainers)

## Context

Contributing to this project has an unusually heavy host requirement. The test
suite exercises four SQL dialects through four async drivers, and one of them,
`aioodbc`, needs the **Microsoft ODBC Driver 18** system library. Installing that
correctly differs on Debian, Fedora, macOS Intel, and Apple Silicon, and getting
it wrong produces an import error several layers away from its cause.

Before this decision the setup contract was "install Python 3.11+, install uv,
install the ODBC driver somehow, start docker compose". In practice that meant:

- The MSSQL suite was silently skipped for most contributors, because the skip
  path is indistinguishable from "driver missing".
- Dependency versions differed per machine, since `uv.lock` was not committed
  (see [ADR 0001](0001-commit-uv-lock.md)).
- The Python version was whatever the host had. CI pins 3.12 for lint, type
  check, and E2E.

A reproducible environment is also a standing engineering requirement for this
project, not a nice-to-have.

## Options considered

### Option A - Documented host setup (status quo)

Keep `docs/contributing/setup.md` as the contract and let contributors install
the toolchain themselves.

- Zero infrastructure to maintain.
- Cannot make the ODBC driver reliable across hosts. The MSSQL provider is one
  of the four isolation backends; leaving it effectively untested for most
  contributors is a real correctness risk, not a convenience issue.
- No reproducibility: "works on my machine" failures are unfalsifiable.

### Option B - Devcontainer with everything pinned (chosen)

A `.devcontainer/` that pins the base image by digest, `uv` by digest, the
devcontainer feature by exact version, and the ODBC packages by exact apt
version, then installs from the committed lock.

- One command to a working environment, including MSSQL.
- Reproducible: every external input is pinned, so a rebuild in six months
  produces the same environment.
- Costs: contributors need Docker and an editor that supports devcontainers;
  the image must be maintained as pins age.

### Option C - Nix or similar declarative environment

Fully reproducible without Docker-in-editor tooling.

- Rejected. It solves the same problem at a much higher adoption cost for a
  FastAPI-ecosystem contributor base, and it does not remove the Docker
  requirement anyway, because the test suite needs real database servers
  (see [ADR 0003](0003-testcontainers-replaces-docker-compose.md)).

## Decision

Adopt a devcontainer as the canonical development environment, with every
external input pinned.

Concretely:

- **Base image pinned by digest** - `mcr.microsoft.com/devcontainers/python:3.12-bookworm@sha256:...`,
  not a floating tag.
- **`uv` copied from its official image, pinned by digest** - `ghcr.io/astral-sh/uv:0.11.32@sha256:...`,
  rather than `curl | sh`, which would resolve to a different version over time.
- **ODBC packages pinned to an exact apt version** (`18.6.2.1-1`), chosen because
  it is published for both `amd64` and `arm64`, so the image builds identically
  on Apple Silicon and on x86 CI runners.
- **Devcontainer feature pinned to an exact version** (`docker-outside-of-docker:1.10.0`)
  with a `devcontainer-lock.json`. A `:1` reference would float across minor releases.
- **Virtualenv and uv cache on named volumes**, outside the bind-mounted workspace.
  On Docker Desktop the workspace is a slow bind mount, and a `.venv` there costs
  seconds on every import. Named volumes are native-speed and survive rebuilds.
- **`docker-outside-of-docker`, not docker-in-docker.** Containers started by the
  test suite are siblings of the devcontainer, sharing the host daemon. This is
  what makes [ADR 0003](0003-testcontainers-replaces-docker-compose.md) work, and it
  is why `TESTCONTAINERS_HOST_OVERRIDE` points at `host.docker.internal`.
- **`post-create.sh` verifies rather than assumes** - it checks the toolchain,
  ODBC driver registration, `pyodbc` import, and Docker reachability, so a broken
  environment fails at creation with a clear message instead of as a confusing
  test error later.

The devcontainer is the *canonical* environment, not the *only* one. Host-based
development continues to work; it simply carries no reproducibility guarantee.

## Consequences

### Positive

- MSSQL is testable by every contributor, closing a real coverage gap on one of
  the four isolation backends.
- The environment matches CI: same Python, same locked dependencies.
- New-contributor setup collapses to "open in container".
- Sibling-container Docker access makes the Testcontainers migration viable.

### Negative

- Docker becomes a hard requirement for the full workflow.
- Pins age. The base image, `uv`, ODBC packages, and the feature all need periodic
  refresh, and nothing currently reminds us to do it.
- The devcontainer pins Python 3.12 with `UV_PYTHON_DOWNLOADS=never`, so the
  3.11 and 3.13 legs of the support matrix cannot be reproduced locally. CI
  still covers them; a contributor debugging a version-specific failure must
  fall back to a host environment.

### Neutral

- No effect on the published package or on consumers. Nothing in `.devcontainer/`
  ships in the wheel.

## Follow-ups

1. `post-create.sh` falls back to an unlocked `uv sync` when `--locked` fails,
   which keeps container creation working but weakens the reproducibility
   guarantee. Deliberate DX trade-off; revisit if drift becomes common.
2. No automated reminder to refresh the digests. Consider a scheduled workflow
   that opens an issue when any pinned digest drifts from its upstream tag.
