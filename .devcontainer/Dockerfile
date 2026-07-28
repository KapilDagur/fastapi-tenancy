# syntax=docker/dockerfile:1
#################################################################################
# fastapi-tenancy — development container                                       #
#                                                                               #
# Every external input is pinned (base image by digest, uv by digest, ODBC by   #
# exact apt version) so that a rebuild on any machine, at any time, produces    #
# the same environment.                                                         #
#################################################################################

FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm@sha256:6ef67a2f0d2f054ad60990679e15208367ff10110a0c0be7f18dbaa7b7319d1d

USER root

#################################################################################
# Microsoft ODBC Driver 18 — required by pyodbc/aioodbc for the MSSQL backend.  #
# Pinned to a version published for BOTH amd64 and arm64 so the image builds    #
# identically on Apple Silicon and on x86 CI runners.                           #
#################################################################################
ARG MSODBCSQL_VERSION=18.6.2.1-1
ARG MSSQL_TOOLS_VERSION=18.6.2.1-1

RUN set -eux; \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg; \
    echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update; \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18="${MSODBCSQL_VERSION}" \
        mssql-tools18="${MSSQL_TOOLS_VERSION}" \
        unixodbc-dev \
        postgresql-client \
        default-mysql-client \
        redis-tools; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# sqlcmd / bcp on PATH for manual inspection of the MSSQL test container.
ENV PATH="/opt/mssql-tools18/bin:${PATH}"

#################################################################################
# uv — copied from the official distroless image and pinned by digest.          #
# Keeping the version here (not "curl | sh") makes the toolchain reproducible.  #
#################################################################################
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c \
     /uv /uvx /usr/local/bin/

#################################################################################
# The virtualenv and uv cache live OUTSIDE the bind-mounted workspace.          #
#                                                                               #
# Why: on Docker Desktop (macOS/Windows) the workspace is a slow bind mount;    #
# a .venv there costs seconds on every import.  Named volumes are native-speed  #
# and survive container rebuilds.  Docker seeds an empty named volume from the  #
# image, so creating these with the right ownership here is what makes the      #
# volumes writable by the non-root user at runtime.                             #
#################################################################################
ENV UV_PROJECT_ENVIRONMENT=/home/vscode/.venv \
    UV_CACHE_DIR=/home/vscode/.cache/uv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN set -eux; \
    mkdir -p /home/vscode/.venv /home/vscode/.cache/uv; \
    chown -R vscode:vscode /home/vscode/.venv /home/vscode/.cache

USER vscode
