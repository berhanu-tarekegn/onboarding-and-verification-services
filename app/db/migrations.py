"""Schema provisioning and migration helpers for schema-based multi-tenant PostgreSQL.

Schema-Based Multi-Tenancy Architecture
---------------------------------------
Each tenant gets their own PostgreSQL schema containing their isolated data.
The public schema contains shared data (tenant registry, baseline templates).

Workflow:
1. When a new tenant is onboarded:
   - Create a dedicated PostgreSQL schema (e.g., tenant_acme)
   - Run Alembic migrations for tenant-specific tables in that schema
   
2. At deploy time (upgrade_all_tenants):
   - Upgrade public schema first (baseline templates, tenant registry)
   - Iterate over all active tenants and upgrade their schemas

3. When a tenant is removed:
   - Optionally drop the tenant's schema (or soft-delete by marking inactive)
"""

import asyncio
import logging
from pathlib import Path
from typing import List

from alembic import command as alembic_cmd
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Path to the alembic.ini at project root
_ALEMBIC_INI = str(Path(__file__).resolve().parents[2] / "alembic.ini")


def _to_sync_url(url: str) -> str:
    """Return the DB URL used by Alembic.

    This project runs Alembic migrations using the asyncpg driver (see
    `alembic/env.py`), so we keep `postgresql+asyncpg://...` URLs intact.
    """
    return url


def _sanitize_schema_name(schema_name: str) -> str:
    """Build the full PostgreSQL schema identifier from a tenant's schema_name.

    Prefixes with 'tenant_' to prevent conflicts with reserved schema names.
    """
    sanitized = "".join(c if c.isalnum() else "_" for c in schema_name.lower())
    return f"tenant_{sanitized}"[:63]


def _make_alembic_config(
    tenant_schema: str | None = None,
    database_url: str | None = None,
) -> AlembicConfig:
    """Build an ``AlembicConfig`` optionally scoped to a tenant schema.

    Parameters
    ----------
    tenant_schema:
        If given, passed as ``-x tenant_schema=<value>`` so that
        ``env.py`` switches ``search_path`` and writes
        ``alembic_version`` into the tenant schema.
    database_url:
        Override the DB URL (useful when the caller already has the
        resolved URL from settings).
    """
    cfg = AlembicConfig(_ALEMBIC_INI)
    if database_url:
        cfg.set_main_option("sqlalchemy.url", _to_sync_url(database_url))
    if tenant_schema:
        cfg.attributes["tenant_schema"] = tenant_schema
        cfg.cmd_opts = type("Opts", (), {"x": [f"tenant_schema={tenant_schema}"]})()  # type: ignore[assignment]
    return cfg


async def provision_tenant_schema(
    tenant_schema_name: str,
    engine: AsyncEngine,
    database_url: str | None = None,
) -> None:
    """Create a dedicated PostgreSQL schema for a new tenant and run migrations."""
    schema_name = _sanitize_schema_name(tenant_schema_name)
    logger.info("Provisioning schema '%s' for tenant schema_name '%s'", schema_name, tenant_schema_name)

    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        logger.info("Schema '%s' created", schema_name)

    await upgrade_tenant_schema(
        tenant_schema_name=tenant_schema_name,
        database_url=database_url,
        revision="head",
    )

    logger.info("Schema '%s' provisioned successfully", schema_name)


async def upgrade_tenant_schema(
    tenant_schema_name: str,
    database_url: str | None = None,
    revision: str = "head",
) -> None:
    """Run Alembic migrations for a specific tenant's schema."""
    schema_name = _sanitize_schema_name(tenant_schema_name)
    logger.info("Upgrading schema '%s' to revision '%s'", schema_name, revision)

    cfg = _make_alembic_config(
        tenant_schema=schema_name,
        database_url=database_url,
    )

    await asyncio.to_thread(alembic_cmd.upgrade, cfg, revision)
    logger.info("Schema '%s' upgraded to '%s'", schema_name, revision)


async def upgrade_public_schema(database_url: str | None = None) -> None:
    """Run Alembic migrations for the public schema only.
    
    This creates/updates shared tables:
    - tenants (tenant registry)
    - baseline_templates (system-owned templates)
    - baseline_template_definitions (template versions)
    """
    logger.info("Upgrading public schema...")
    cfg = _make_alembic_config(database_url=database_url)
    await asyncio.to_thread(alembic_cmd.upgrade, cfg, "head")
    logger.info("Public schema upgraded")


async def upgrade_all_tenants(
    engine: AsyncEngine,
    database_url: str | None = None,
) -> None:
    """Run migrations for public schema and all active tenant schemas.

    This is the main entry point for deploy-time migrations.
    
    Order of operations:
    1. Upgrade public schema first (creates shared tables)
    2. Query all active tenants from public.tenants
    3. Upgrade each tenant's schema in parallel (or sequentially for safety)
    """
    from app.models.public.tenant import Tenant
    from sqlmodel.ext.asyncio.session import AsyncSession
    
    # Step 1: Upgrade public schema
    await upgrade_public_schema(database_url=database_url)
    
    # Step 2: Get all active tenants
    async with AsyncSession(engine) as session:
        result = await session.execute(
            select(Tenant).where(Tenant.is_active == True)
        )
        tenants = result.scalars().all()
    
    if not tenants:
        logger.info("No active tenants to upgrade")
        return
    
    # Step 3: Upgrade each tenant schema
    logger.info("Upgrading %d tenant schema(s)...", len(tenants))
    
    for tenant in tenants:
        try:
            await upgrade_tenant_schema(
                tenant_schema_name=tenant.schema_name,
                database_url=database_url,
            )
        except Exception as e:
            logger.error(
                "Failed to upgrade schema for tenant '%s': %s",
                tenant.schema_name,
                str(e),
            )
            raise
    
    logger.info("All tenant schemas upgraded successfully")


async def drop_tenant_schema(
    tenant_schema_name: str,
    engine: AsyncEngine,
    cascade: bool = False,
) -> None:
    """Drop a tenant's PostgreSQL schema.

    WARNING: This permanently deletes all data in the tenant's schema.
    """
    schema_name = _sanitize_schema_name(tenant_schema_name)
    cascade_clause = "CASCADE" if cascade else "RESTRICT"

    logger.warning(
        "Dropping schema '%s' for tenant schema_name '%s' (cascade=%s)",
        schema_name,
        tenant_schema_name,
        cascade,
    )

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} {cascade_clause}"))

    logger.info("Schema '%s' dropped", schema_name)


async def list_tenant_schemas(engine: AsyncEngine) -> List[str]:
    """List all tenant schemas in the database.
    
    Returns schema names that match the 'tenant_*' pattern.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name LIKE 'tenant_%'
                ORDER BY schema_name
            """)
        )
        return [row[0] for row in result.fetchall()]
