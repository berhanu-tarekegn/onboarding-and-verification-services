"""Alembic environment — schema-aware migrations for multi-tenant PostgreSQL.

Schema-Based Multi-Tenancy Migration Strategy
---------------------------------------------
This environment supports two modes of operation:

1. Public Schema Migrations (default):
   - Run without `-x tenant_schema=...`
   - Creates shared tables: tenants, baseline_templates, etc.
   - alembic_version table lives in public schema
   
2. Tenant Schema Migrations:
   - Run with `-x tenant_schema=tenant_acme`
   - Creates tenant-specific tables: tenant_templates, etc.
   - Sets `search_path TO <tenant_schema>, public` before running
   - alembic_version table lives inside the tenant schema
   - Allows FKs to reference public.baseline_templates

Usage Examples:
    # Migrate public schema (run once at deployment)
    alembic upgrade head
    
    # Migrate a specific tenant schema (run per tenant)
    alembic -x tenant_schema=tenant_acme upgrade head

Programmatic Usage:
    The `app.db.migrations` module provides async wrappers that call
    Alembic with the appropriate arguments.
"""

from logging.config import fileConfig
from typing import Optional
import asyncio

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# ── Import app config & all models so metadata is populated ──────────
from app.core.config import get_settings

# Import all models to ensure SQLModel registers them in metadata
from app.models import public as _public_models  # noqa: F401
from app.models import tenant as _tenant_models  # noqa: F401

from sqlmodel import SQLModel

settings = get_settings()

# ── Alembic objects ──────────────────────────────────────────────────
config = context.config

# Override sqlalchemy.url from our app settings if not already provided
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ── Helpers ──────────────────────────────────────────────────────────

def _to_sync_url(url: str) -> str:
    """Convert an async DB URL to a synchronous one for Alembic."""
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2")
    return url


def _get_tenant_schema() -> Optional[str]:
    """Get the tenant schema from command line or config attributes.
    
    Returns None for public schema migrations, or the schema name for tenant migrations.
    """
    # Check config attributes first (set programmatically)
    if hasattr(config, "attributes") and "tenant_schema" in config.attributes:
        return config.attributes["tenant_schema"]
    
    # Fall back to -x argument
    x_args = context.get_x_argument(as_dictionary=True)
    tenant_schema = x_args.get("tenant_schema")
    if tenant_schema is None:
        return None
    normalized = str(tenant_schema).strip()
    if not normalized:
        return None
    if normalized.lower() == "public":
        return None
    return normalized


# ── Offline mode ─────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = _to_sync_url(config.get_main_option("sqlalchemy.url"))
    tenant_schema = _get_tenant_schema()
    
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=tenant_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ─────────────────────────────────────────────────────

def do_run_migrations(connection: Connection) -> None:
    """Configure and run migrations against an established connection."""
    tenant_schema = _get_tenant_schema()

    # Fail fast instead of "freezing" the DB when locks can't be acquired.
    # This is especially important when running migrations while the API (or
    # other clients) are connected.
    connection.execute(text("SET lock_timeout TO '5s'"))
    connection.execute(text("SET statement_timeout TO '5min'"))
    
    # Set search_path for tenant schema migrations
    if tenant_schema:
        connection.execute(text(f"SET search_path TO {tenant_schema}, public"))

    # NOTE: In SQLAlchemy 2.x, the statements above can trigger an implicit
    # transaction on the connection. If we don't commit here, Alembic may run
    # migrations in that implicit transaction and then the outer async context
    # manager can roll everything back on exit. Commit now so Alembic controls
    # its own transaction via `context.begin_transaction()`.
    connection.commit()

    # Pass target_metadata=None: all migrations are written explicitly so
    # autogenerate is not needed. Passing the real SQLModel metadata causes
    # SQLAlchemy to fire before_create events for registered enum types
    # (e.g. TemplateType) during create_table, producing duplicate CREATE TYPE.
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table_schema=tenant_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the asyncpg driver.

    We intentionally avoid creating a synchronous psycopg2 engine because some
    environments have libpq/DSN quirks (or special-character passwords) that
    can break psycopg2 connections while asyncpg works fine for the app.
    """
    tenant_schema = _get_tenant_schema()

    async def _run() -> None:
        connectable = create_async_engine(
            config.get_main_option("sqlalchemy.url"),
            poolclass=pool.NullPool,
        )
        async with connectable.connect() as connection:
            if tenant_schema:
                await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {tenant_schema}"))
                await connection.commit()
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(_run())


# ── Entrypoint ───────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
