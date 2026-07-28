"""Ephemeral test services via ``testcontainers``.

This module replaces the old ``docker-compose.test.yml`` + TCP-probe approach
(see ``docs/contributing/adr/0003-testcontainers-replaces-docker-compose.md``).
Each backend is started *lazily* on first use, shared across the whole pytest
session, and torn down by ``pytest_unconfigure`` in :mod:`tests.conftest`.

Design contract
---------------
* **Lazy and memoised** — a container is started only when a test actually asks
  for its URL, and exactly once per session.  Unit-only runs touch nothing.
* **Skip, never fail, when Docker is missing** — if the Docker daemon is
  unavailable (or a container refuses to start) the accessor raises
  ``pytest.skip``.  Contributors without Docker keep a green unit run, exactly
  as the previous probe-based code allowed.  The first failure is memoised so
  later tests skip immediately instead of retrying a doomed start.
* **Pinned by digest** — images default to SHA-pinned refs.  Override per
  backend with the ``FT_TEST_<BACKEND>_IMAGE`` environment variables; CI uses
  this to drive the PostgreSQL version matrix.
* **Stable credentials** — ``testing`` / ``Testing123!`` / ``test_db`` so the
  generated URLs match what the suite has always used.

Callers use the four accessors — :func:`postgres_url`, :func:`mysql_url`,
:func:`mssql_url`, :func:`redis_url` — which return a ready SQLAlchemy / Redis
URL or skip the current test.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from testcontainers.core.container import DockerContainer

#####################################################################
# Test credentials — intentionally fixed so generated URLs are      #
# deterministic and diffable against the pre-migration hardcoded    #
# values.  These are throwaway container credentials, never real.   #
#####################################################################

_DB_USER = "testing"
_DB_PASSWORD = "Testing123!"
_DB_NAME = "test_db"

#############################################################################
# Default images, pinned by digest.                                         #
#                                                                           #
# Refresh a digest with:                                                    #
#   docker buildx imagetools inspect <tag> --format '{{.Manifest.Digest}}'  #
#############################################################################

_DEFAULT_IMAGES = {
    "postgres": (
        "postgres:16-alpine@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229"
    ),
    "mysql": ("mysql:8.0@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b"),
    "mssql": (
        "mcr.microsoft.com/mssql/server:2022-latest@sha256:"
        "e07b9699a2b749969f19d86563ceeea22bd3a69f7f1db85a8d1ac4bdaf0c6f56"
    ),
    "redis": (
        "redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99"
    ),
}


def _image(backend: str) -> str:
    """Return the container image ref for *backend*, honouring env overrides."""
    return os.getenv(f"FT_TEST_{backend.upper()}_IMAGE", _DEFAULT_IMAGES[backend])


#####################################################################
# Session-scoped memos                                              #
#####################################################################

#: backend -> running container (retained so teardown can stop it).
_containers: dict[str, DockerContainer] = {}
#: backend -> resolved connection URL.
_urls: dict[str, str] = {}
#: backend -> skip reason, so a failed start is not retried per test.
_skips: dict[str, str] = {}


def _ensure(backend: str, starter: Callable[[], tuple[DockerContainer, str]]) -> str:
    """Start (once) the container for *backend* and return its URL, or skip.

    Args:
        backend: Backend key (``"postgres"``, ``"mysql"``, ``"mssql"``, ``"redis"``).
        starter: Callable that starts the container and returns ``(container, url)``.

    Returns:
        The connection URL for the running container.
    """
    if backend in _urls:
        return _urls[backend]
    if backend in _skips:
        pytest.skip(_skips[backend])

    try:
        container, url = starter()
    except Exception as exc:
        reason = (
            f"{backend} testcontainer could not start ({type(exc).__name__}: {exc}). "
            "Is the Docker daemon running?"
        )
        _skips[backend] = reason
        pytest.skip(reason)

    _containers[backend] = container
    _urls[backend] = url
    return url


##############
# PostgreSQL #
##############


def _start_postgres() -> tuple[DockerContainer, str]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    container = PostgresContainer(
        image=_image("postgres"),
        username=_DB_USER,
        password=_DB_PASSWORD,
        dbname=_DB_NAME,
        driver="asyncpg",
    )
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    url = f"postgresql+asyncpg://{_DB_USER}:{_DB_PASSWORD}@{host}:{port}/{_DB_NAME}"
    return container, url


def postgres_url() -> str:
    """Return an asyncpg URL for the session-shared PostgreSQL, or skip."""
    return _ensure("postgres", _start_postgres)


#########
# MySQL #
#########


def _start_mysql() -> tuple[DockerContainer, str]:
    from testcontainers.community.mysql import MySqlContainer  # noqa: PLC0415

    container = MySqlContainer(
        image=_image("mysql"),
        username=_DB_USER,
        password=_DB_PASSWORD,
        dbname=_DB_NAME,
    )
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(3306)
    url = f"mysql+aiomysql://{_DB_USER}:{_DB_PASSWORD}@{host}:{port}/{_DB_NAME}"
    return container, url


def mysql_url() -> str:
    """Return an aiomysql URL for the session-shared MySQL, or skip."""
    return _ensure("mysql", _start_mysql)


##############
# SQL Server #
##############

# Driven with the generic DockerContainer rather than testcontainers'
# SqlServerContainer so we control readiness with a real pyodbc probe and
# create ``test_db`` ourselves — the mssql/server image has no MSSQL_DATABASE
# equivalent, which is the reason the old compose/mssql/ wrapper existed.

_MSSQL_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
_MSSQL_READY_TIMEOUT_S = 120


def _mssql_pyodbc_dsn(host: str, port: str | int, database: str = "master") -> str:
    """Build a pyodbc DSN for the readiness probe."""
    return (
        f"DRIVER={{{_MSSQL_ODBC_DRIVER}}};SERVER={host},{port};"
        f"DATABASE={database};UID=sa;PWD={_DB_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=yes;"
    )


def _start_mssql() -> tuple[DockerContainer, str]:
    import time  # noqa: PLC0415

    import pyodbc  # noqa: PLC0415 — provided transitively by the aioodbc (mssql) extra
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    container = (
        DockerContainer(_image("mssql"))
        .with_env("ACCEPT_EULA", "Y")
        .with_env("MSSQL_SA_PASSWORD", _DB_PASSWORD)
        .with_env("MSSQL_PID", "Developer")
        .with_exposed_ports(1433)
    )
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(1433)

    # SQL Server accepts TCP long before it accepts logins, so poll with a real
    # connection, then create the test database.
    deadline = time.monotonic() + _MSSQL_READY_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = pyodbc.connect(_mssql_pyodbc_dsn(host, port), autocommit=True, timeout=5)
            try:
                conn.execute(
                    f"IF DB_ID('{_DB_NAME}') IS NULL "
                    f"CREATE DATABASE {_DB_NAME} "
                    "COLLATE Latin1_General_100_CI_AS_SC_UTF8;"
                )
            finally:
                conn.close()
            break
        except pyodbc.Error as exc:  # not ready yet — retry
            last_err = exc
            time.sleep(2)
    else:
        container.stop()
        msg = f"SQL Server never became ready within {_MSSQL_READY_TIMEOUT_S}s: {last_err!r}"
        raise RuntimeError(msg)

    url = (
        f"mssql+aioodbc://sa:{_DB_PASSWORD}@{host}:{port}/{_DB_NAME}"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    )
    return container, url


def mssql_url() -> str:
    """Return an aioodbc URL for the session-shared SQL Server, or skip.

    Requires the *ODBC Driver 18 for SQL Server* system library — the same
    requirement the suite has always had for the ``mssql`` extra.
    """
    return _ensure("mssql", _start_mssql)


#########
# Redis #
#########


def _start_redis() -> tuple[DockerContainer, str]:
    from testcontainers.community.redis import RedisContainer  # noqa: PLC0415

    container = RedisContainer(image=_image("redis"))
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    url = f"redis://{host}:{port}"
    return container, url


def redis_url(db: int = 0) -> str:
    """Return a Redis URL selecting logical DB *db*, or skip.

    Args:
        db: Logical Redis database index.
    """
    base = _ensure("redis", _start_redis)
    return f"{base}/{db}"


############
# Teardown #
############


def stop_all() -> None:
    """Stop every started container.  Called from ``pytest_unconfigure``."""
    while _containers:
        container = _containers.popitem()[1]
        # Best-effort cleanup: a container that already died must not mask the
        # test result that is being reported alongside this teardown.
        with contextlib.suppress(Exception):
            container.stop()
    _urls.clear()
