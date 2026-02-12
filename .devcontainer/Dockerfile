FROM mcr.microsoft.com/devcontainers/python:3.11-bullseye

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# Remove broken Yarn repo added by base image/features
RUN rm -f /etc/apt/sources.list.d/yarn.list

# Install system dependencies
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    postgresql-client \
    redis-tools \
    build-essential \
    libpq-dev \
    git \
    curl \
    vim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install UV
USER vscode
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

# Install development tools
RUN pip install --no-cache-dir ipython ipdb

# Set working directory
WORKDIR /workspace

# Copy project files
COPY pyproject.toml uv.lock* ./

# Install dependencies with UV
RUN uv pip install -e ".[dev,test]" || echo "Will install on container start"

# Set shell
SHELL ["/bin/bash", "-c"]