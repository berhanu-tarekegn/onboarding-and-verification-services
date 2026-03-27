"""CLI script to run migrations for all tenants."""

import asyncio
import logging
import sys

from app.db.session import engine
from app.db.migrations import upgrade_all_tenants, _ALEMBIC_INI
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    settings = get_settings()
    logger.info("Starting migrations for all tenants...")
    try:
        await upgrade_all_tenants(engine=engine, database_url=settings.DATABASE_URL)
        logger.info("Migration completed successfully.")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())