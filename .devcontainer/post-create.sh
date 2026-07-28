#!/usr/bin/env bash
#################################################################################
# Runs once, after the container is created.                                    #
#                                                                               #
# Installs the locked dependency set and verifies that everything the test      #
# suite needs is actually present, so a broken environment fails here with a    #
# clear message rather than as a confusing test error later.                    #
#################################################################################
set -euo pipefail

VENV="${UV_PROJECT_ENVIRONMENT:-/home/vscode/.venv}"
CACHE="${UV_CACHE_DIR:-/home/vscode/.cache/uv}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mwarn\033[0m %s\n' "$*"; }

#################################################################################
# Named volumes are created empty and root-owned on some Docker versions;       #
# reassert ownership so uv can write to them as the non-root user.              #
#################################################################################
log "Preparing volumes"
sudo chown -R "$(id -u):$(id -g)" "$VENV" "$CACHE" 2>/dev/null || true
ok "$VENV"
ok "$CACHE"

#################################################################################
# Dependencies.  --locked fails if uv.lock is missing or out of step with       #
# pyproject.toml, which is the behaviour we want in a reproducible container.   #
#################################################################################
log "Installing dependencies from uv.lock"
if ! uv sync --all-extras --locked; then
    warn "uv.lock is missing or stale — resolving fresh and writing a new lock."
    warn "Commit the updated uv.lock so this container is reproducible."
    uv sync --all-extras
fi

#################################################################################
# Verification                                                                  #
#################################################################################
log "Verifying toolchain"
printf '    python  %s\n' "$("$VENV/bin/python" --version 2>&1)"
printf '    uv      %s\n' "$(uv --version 2>&1)"
printf '    ruff    %s\n' "$(uv run ruff --version 2>&1)"
printf '    mypy    %s\n' "$(uv run mypy --version 2>&1)"

log "Verifying MSSQL ODBC driver"
if odbcinst -q -d | grep -q "ODBC Driver 18 for SQL Server"; then
    ok "ODBC Driver 18 for SQL Server registered"
    if uv run python -c "import pyodbc" 2>/dev/null; then
        ok "pyodbc imports cleanly"
    else
        warn "pyodbc failed to import — MSSQL tests will skip"
    fi
else
    warn "ODBC Driver 18 not registered — MSSQL tests will skip"
fi

log "Verifying Docker access (required by Testcontainers)"
if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null))"
else
    warn "docker daemon NOT reachable — every container-backed test will skip."
    warn "Check that the host Docker socket is mounted and the daemon is running."
fi

cat <<'EOF'

  Environment ready.

    make check      lint + type + security
    make test       unit tests
    make test-all   full suite (databases start automatically via Testcontainers)

EOF
