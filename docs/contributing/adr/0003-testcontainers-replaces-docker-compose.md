# ADR 0003 - Testcontainers replaces docker-compose for test services

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** fastapi-tenancy maintainers
- **Supersedes:** `docker-compose.test.yml`, `compose/mssql/`, and the `make docker-up` / `docker-down` targets
- **Related:** [0002](0002-devcontainer-canonical-environment.md) (devcontainer)

## Context

The suite needs four real service backends - PostgreSQL, MySQL, SQL Server, and
Redis - because the isolation providers depend on genuine server behaviour that
cannot be faked: PostgreSQL RLS policies and GUCs, `SET LOCAL search_path`
semantics across pooled connections, MSSQL `schema_translate_map`, MySQL's
schema-equals-database model, and Redis Lua atomicity.

Those services were provided by `docker-compose.test.yml` plus TCP probes in the
test fixtures:

```python
def _pg_up() -> bool:
    return _tcp_ok("localhost", 5432)

if not _pg_up():
    pytest.skip("PostgreSQL not reachable on localhost:5432")
```

This worked, but had four structural problems:

1. **Two sources of truth, three in CI.** Service definitions lived in
   `docker-compose.test.yml` for local runs *and* in GitHub Actions `services:`
   blocks for CI, with connection URLs duplicated a third time as `env:` entries
   and as defaults hardcoded in five test modules. The compose file pinned image
   digests; the CI `services:` blocks used floating tags. Local and CI were
   testing against different image versions with no mechanism to notice.

2. **A TCP probe is not a readiness check.** `_tcp_ok` returns true as soon as
   the port accepts a connection. SQL Server accepts TCP long before it accepts
   logins, which is exactly why the compose file needed a 60-second
   `start_period` and a bespoke `sqlcmd` health probe, and why `compose/mssql/`
   existed at all.

3. **Fixed host ports.** 5432, 3306, 1433, and 6379 were claimed on the host.
   A contributor with a local PostgreSQL either got a port conflict or, worse,
   silently ran the destructive schema and database tests against their own
   development server.

4. **Shared mutable state across runs.** Named volumes (`ft_pg_data`,
   `ft_mysql_data`, `ft_mssql_data`) persisted between runs, so a test that
   leaked a schema or database could influence a later run. `make docker-down`
   removed them, but nothing enforced running it.

## Options considered

### Option A - Keep docker-compose, pin digests everywhere (status quo, hardened)

Pin the CI `services:` images to the same digests as the compose file and keep
both.

- Smallest change; keeps `make docker-up` muscle memory.
- Does not fix duplication, it institutionalises it: three places to update for
  every image bump, kept in step by discipline alone.
- Does not fix fixed ports, readiness, or state leakage.

### Option B - Testcontainers for all four backends (chosen)

Delete the compose file and the CI `services:` blocks. Start each backend from
the test session on first use, via a single `tests/_services.py` module.

- One definition of every service, in Python, used identically by local runs,
  the devcontainer, and CI.
- Random host ports, so no conflict with a developer's own services and no risk
  of running destructive tests against them.
- Fresh containers per session: no persistent volumes, no cross-run leakage.
- Real readiness checks instead of TCP probes.
- Costs: Docker becomes required for integration and E2E runs, container startup
  is paid per session, and `testcontainers` becomes a dev dependency.

### Option C - Testcontainers in CI only, compose locally

- Rejected. It keeps both systems and reintroduces the local/CI divergence that
  motivates this decision.

## Decision

Use Testcontainers for all four backends. Delete `docker-compose.test.yml`,
`compose/mssql/`, the `make docker-up` / `docker-down` targets, and the
`services:` blocks in CI.

Design contract for `tests/_services.py`:

- **Lazy and memoised.** A container starts only when a test actually requests
  its URL, and exactly once per session. A unit-only run starts nothing, so the
  fast inner loop stays fast.
- **Skip, never fail, when Docker is absent.** If the daemon is unavailable or a
  container refuses to start, the accessor raises `pytest.skip` and records the
  reason so subsequent tests skip immediately rather than retrying. This
  preserves the property the TCP probes gave us: contributors without Docker
  keep a green unit run.
- **Images pinned by digest**, overridable per backend with `FT_TEST_<BACKEND>_IMAGE`.
  CI uses that override to drive the PostgreSQL 15/16 matrix, so the matrix is
  expressed as an environment variable rather than a duplicated service block.
- **Stable credentials** - `testing` / `Testing123!` / `test_db` - so generated
  URLs match what the suite has always used and the diff stays reviewable.
- **Explicit readiness for SQL Server.** MSSQL is driven with the generic
  `DockerContainer` rather than the library's `SqlServerContainer`, so we can
  poll with a real `pyodbc` connection and create `test_db` ourselves. The
  official image has no `MSSQL_DATABASE` equivalent, which is the entire reason
  `compose/mssql/` existed.
- **Session teardown** via `pytest_unconfigure` in the root `conftest.py`, so
  containers stop regardless of which test package started them.

Test fixtures consume four accessors - `postgres_url()`, `mysql_url()`,
`mssql_url()`, `redis_url()` - which replace every TCP probe, every
`os.getenv("POSTGRES_URL", ...)` default, and every hardcoded `localhost:PORT`
literal in the suite.

## Consequences

### Positive

- One definition per service, shared by local, devcontainer, and CI.
- No host port conflicts; no possibility of a destructive test hitting a
  contributor's own database.
- Fresh state per session by construction.
- CI loses three `services:` blocks and three sets of duplicated `env:` URLs.
- The `TESTCONTAINERS_*` variables already set by [ADR 0002](0002-devcontainer-canonical-environment.md)
  become meaningful instead of inert, and `post-create.sh` stops advertising
  behaviour the repository did not have.

### Negative

- Docker is now required for integration and E2E runs. Previously a contributor
  could point the suite at an already-running service. Unit runs are unaffected.
- Container startup cost is paid once per session rather than once per working
  day with a long-lived compose stack. `TESTCONTAINERS_REUSE_ENABLE=true` in the
  devcontainer mitigates this for the inner loop.
- `testcontainers` and its transitive `docker` SDK join the dev dependency set.
- Ryuk, the Testcontainers reaper, runs as an extra container per session.

### Neutral

- No change to what is tested or to any assertion. This is an infrastructure
  swap; the same fixtures yield the same URLs to the same tests.
- No effect on the published package.

## Notes discovered during implementation

**The Redis service containers were already dead weight.** The Redis suite
exercises `RedisTenantStore` against an in-process `FakeRedis` defined in
`tests/storage/conftest.py`; nothing in `tests/` ever opened a real Redis
connection. CI nevertheless started a Redis service container in both the
`integration` and `e2e-postgres` jobs and exported a `REDIS_URL` that no test
read. Removing those blocks costs no coverage.

`redis_url()` is still provided by `tests/_services.py` so the four-backend
contract is complete and a future real-Redis test has an obvious hook. It had
**no caller** when this ADR was written, with the note that leaving it unused
indefinitely was the worst of the three options.

**Update:** it now has one. `tests/test_rate_limit_lua.py` runs the rate-limit
Lua script against a real Redis server, which is how a total limiter bypass was
found — the deny branch returned the pre-state count, and because every other
rate-limit test mocked `redis.eval`, the script had never actually executed.
`FakeRedis` would not have caught it: the bug lived in the Lua and in its
interaction with the host-side comparison.

**`pyodbc` has no type stubs.** The SQL Server readiness probe imports it, and
no maintained `types-pyodbc` resolves across the project's full
`requires-python` range, so `pyproject.toml` carries a scoped
`ignore_missing_imports` override for that one module rather than relaxing the
global mypy setting.

## Follow-ups

1. Image digests are pinned in `tests/_services.py`. They need the same periodic
   refresh as the devcontainer pins in [ADR 0002](0002-devcontainer-canonical-environment.md);
   ideally one mechanism covers both.
2. MSSQL readiness polls for up to 120 s on a cold image pull. If CI E2E ever
   covers MSSQL, budget for it or pre-pull the image.
