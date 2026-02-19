FROM mcr.microsoft.com/devcontainers/python:3.12-bullseye

# System packages for asyncpg and aiomysql native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# The rest is handled by postCreateCommand in devcontainer.json
