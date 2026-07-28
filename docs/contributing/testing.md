---
title: Running Tests
description: How to run the full test suite for fastapi-tenancy.
---

# Running Tests

## Quick commands

| Command | Description |
|---------|-------------|
| `make test` | Unit tests only (`-m unit`) — no Docker |
| `make test-int` | Integration tests (`-m integration`) |
| `make test-e2e` | End-to-end tests (`-m e2e`) — starts containers on demand |
| `make test-all` | Everything, teed to `test-results.txt` |
| `make coverage` | Full suite + HTML report at `htmlcov/index.html` |

Service containers are started on demand by the suite via Testcontainers
(see [ADR 0003](adr/0003-testcontainers-replaces-docker-compose.md)); there is no compose file and nothing to start by hand.
Docker only needs to be running.

!!! warning "`-m integration` is not what CI runs"

    CI's integration job runs `-m "not e2e"` with the PostgreSQL, MySQL, and
    MSSQL store files ignored — no containers, a few seconds. Selecting on the
    `integration` marker alone also picks up tests that are parametrised
    `e2e` and will start PostgreSQL and MySQL containers.

## Running specific tests

```bash
# A single file
pytest tests/test_manager.py

# A specific test
pytest tests/test_manager.py::TestRateLimitFailClosed

# By marker
pytest -m unit
pytest -m integration
pytest -m "not slow"
```

!!! note "`-q` is already in `addopts`"

    Passing `-q` again makes it `-qq`, which suppresses the `N passed` summary
    line entirely — you get dots and nothing else. Omit it.

## Coverage

Branch coverage is collected by the integration and e2e targets and reported
at `htmlcov/index.html`. There is no `fail_under` gate configured; treat the
number as a signal, not a pass/fail.

```bash
make coverage
```

Reading it: `isolation/*` and `storage/{database,redis}` look low in the
no-services selection because that job deliberately skips the paths needing a
real backend. Run `make test-all` for the true figure.

## Regression-test docstring convention

Every test class that pins behaviour against a specific incident or
review finding starts its docstring with `FIX (<id>):` and includes a
short *Before / After* section explaining the failure mode the fix
addresses:

```python
class TestRateLimitScriptEnforcesTheLimit:
    """FIX (S3): the Lua deny branch returned the pre-state count.

    Before the fix:
        ``if count >= limit then return count end`` combined with the
        host-side ``count > limit`` check meant the limiter never denied.

    After the fix:
        The branch returns ``count + 1`` so the host-side check fires.
    """
```

`<id>` is the issue tracker tag, code-review finding ID, or CHANGELOG
section anchor — anything that lets a future reader find the original
context in three clicks. Examples:

| Tag | Source of the ID |
| --- | --- |
| `FIX (S1):` / `FIX (C2):` | code-review finding (Security #1, Correctness #2) |
| `FIX (#142):` | GitHub issue / PR number |
| `FIX (CVE-2026-12345):` | upstream advisory |
| `FIX:` | informal, no tracking — acceptable for incidents pre-dating this convention |

The convention makes regression tests **searchable** — `grep -rn '"""FIX'
tests/` shows every test class that exists to prevent a specific
regression. CI doesn't enforce it; reviewers do.

If you are touching a regression test that pre-dates this convention,
upgrading its docstring is a welcome drive-by edit.
