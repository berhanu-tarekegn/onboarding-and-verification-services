"""CLI script to run database migrations.

Usage:
    ./.venv/bin/python scripts/migrate.py
    ./.venv/bin/python scripts/migrate.py --tenant abyssinia_corp
    ./.venv/bin/python scripts/migrate.py --public-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import get_settings
from app.db.migrations import (
    upgrade_all_tenants,
    upgrade_public_schema,
    upgrade_tenant_schema,
)
from app.db.session import get_engine


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run public and tenant schema migrations.")
    parser.add_argument(
        "--tenant",
        help="Upgrade only one tenant schema by tenant_key, e.g. 'abyssinia_corp'.",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Upgrade only the public schema.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = get_settings()

    if args.tenant and args.public_only:
        logger.error("--tenant and --public-only cannot be used together.")
        return 2

    if args.public_only:
        logger.info("Upgrading public schema only...")
        await upgrade_public_schema(database_url=settings.DATABASE_URL)
        logger.info("Public schema migration completed successfully.")
        return 0

    if args.tenant:
        logger.info("Upgrading tenant schema for tenant_key=%s...", args.tenant)
        await upgrade_tenant_schema(
            tenant_schema_name=args.tenant,
            database_url=settings.DATABASE_URL,
        )
        logger.info("Tenant schema migration completed successfully.")
        return 0

    logger.info("Upgrading public schema and all tenant schemas...")
    await upgrade_all_tenants(
        engine=get_engine(settings.DATABASE_URL),
        database_url=settings.DATABASE_URL,
    )
    logger.info("All schema migrations completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
