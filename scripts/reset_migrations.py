#!/usr/bin/env python3
"""Reset alembic migrations and re-run from scratch.

Use this when migration history is out of sync with the database.

Usage:
    uv run python scripts/reset_migrations.py
    
    # Or with docker
    docker compose -f docker-compose.dev.yaml exec api uv run python scripts/reset_migrations.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def reset_migrations():
    """Drop all tables and reset alembic_version."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    
    print(f"Connecting to: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else 'database'}")
    
    async with engine.begin() as conn:
        # Check if alembic_version exists
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'alembic_version'
            )
        """))
        exists = result.scalar()
        
        if exists:
            # Get current version
            result = await conn.execute(text("SELECT version_num FROM public.alembic_version"))
            versions = [row[0] for row in result.fetchall()]
            print(f"Current alembic versions: {versions}")
            
            # Clear the version table
            await conn.execute(text("DELETE FROM public.alembic_version"))
            print("Cleared alembic_version table")
        else:
            print("No alembic_version table found")
        
        # List existing schemas
        result = await conn.execute(text("""
            SELECT schema_name FROM information_schema.schemata 
            WHERE schema_name LIKE 'tenant_%'
        """))
        tenant_schemas = [row[0] for row in result.fetchall()]
        
        if tenant_schemas:
            print(f"Found tenant schemas: {tenant_schemas}")
            print("Note: You may need to clear alembic_version in each tenant schema as well")
            
            for schema in tenant_schemas:
                try:
                    await conn.execute(text(f"DELETE FROM {schema}.alembic_version"))
                    print(f"  Cleared {schema}.alembic_version")
                except Exception as e:
                    print(f"  No alembic_version in {schema} (or error: {e})")
    
    await engine.dispose()
    print("\nDone! Now run: alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(reset_migrations())
