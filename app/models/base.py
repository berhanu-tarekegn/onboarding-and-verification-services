"""Base model classes for schema-based multi-tenancy architecture.

This module provides foundational classes for:
- AuditBase: Audit trail fields (created_at, updated_at, created_by, updated_by)
- PublicSchemaModel: Models that live in the public schema (Tenant, BaselineTemplate)
- TenantSchemaModel: Models that live in per-tenant schemas (TenantTemplate)

Schema Strategy:
- Public schema: Shared/global data accessible to all tenants (read-only for tenants)
- Tenant schemas: Isolated data per tenant (full CRUD for tenant's own data)
"""

from datetime import datetime, timezone
from typing import ClassVar

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.core.context import user_context


class AuditBase(SQLModel):
    """Base model adding audit trail fields to derived models.
    
    All models inherit audit fields for tracking creation and modification.
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_type=sa.DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={
            "onupdate": lambda: datetime.now(timezone.utc),
        },
    )
    created_by: str = Field(
        default_factory=user_context.get,
        nullable=False,
        max_length=255,
    )
    updated_by: str = Field(
        default_factory=user_context.get,
        nullable=False,
        max_length=255,
        sa_column_kwargs={
            "onupdate": user_context.get,
        },
    )


class PublicSchemaModel(AuditBase):
    """Base model for entities that live in the public schema.
    
    Used for:
    - Tenant registry (system-wide)
    - Baseline templates (system-owned, immutable by tenants)
    
    These models are accessible to all tenants but only modifiable by system admins.
    """

    __table_args__: ClassVar[dict] = {"schema": "public"}


class TenantSchemaModel(AuditBase):
    """Base model for entities that live in per-tenant schemas.
    
    Used for:
    - Tenant-specific templates (can extend baselines)
    - Tenant-specific configurations
    
    The actual schema is determined at runtime via search_path.
    Models inheriting from this class do NOT specify a schema - the schema
    is set dynamically when the session is created.
    """
    
    pass
