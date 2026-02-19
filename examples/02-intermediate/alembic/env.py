"""Alembic environment configuration for per-tenant schema migrations.

Usage:
    # Run for a specific tenant schema:
    TENANT_SCHEMA=tenant_acme_corp alembic upgrade head

    # Or use TenantMigrationManager which handles all tenants:
    from fastapi_tenancy.migrations.manager import TenantMigrationManager
    mgr = TenantMigrationManager(config)
    await mgr.upgrade_all_tenants()
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

TENANT_SCHEMA = os.getenv("TENANT_SCHEMA", "public")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=TENANT_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(
            __import__("sqlalchemy").text(f"SET search_path TO {TENANT_SCHEMA}, public")
        )
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=TENANT_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
