"""Product models — tenant-owned products with a single KYC template association.

Products represent onboarding offerings (e.g. Savings Account, Wallet, SME Account).
Each product is linked to exactly one TenantTemplate which defines the KYC
requirements for that product.

Two onboarding flows coexist:
- General onboarding: TenantTemplate used directly on a Submission (no product needed)
- Product-specific: Product references a TenantTemplate via template_id; Submission
  stores product_id for traceability

Product lifecycle:
    DRAFT → ACTIVE → INACTIVE
    DRAFT → (delete)
    INACTIVE → ACTIVE (re-activate)
"""

import uuid as _uuid
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum, Text
from sqlmodel import Field
from uuid_extensions import uuid7

from app.models.base import TenantSchemaModel


class ProductStatus(str, Enum):
    """Lifecycle status for a product."""

    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class Product(TenantSchemaModel, table=True):
    """A tenant-owned onboarding product.

    Each product is linked to exactly one TenantTemplate that defines the KYC
    configuration for onboarding sessions under this product.

    product_code is unique within the tenant schema and is used as a stable
    machine-readable identifier (e.g. SAVINGS_ACC, WALLET_V2).

    template_id is optional at creation time (product starts in DRAFT) but is
    required before the product can be activated.
    """

    __tablename__ = "products"

    id: _uuid.UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
    )

    name: str = Field(index=True, max_length=255)

    product_code: str = Field(
        index=True,
        max_length=100,
        description="Machine-readable code, unique within the tenant.",
    )

    description: Optional[str] = Field(default=None, sa_column=Column(Text))

    status: ProductStatus = Field(
        default=ProductStatus.DRAFT,
        sa_column=Column(
            SAEnum(
                ProductStatus,
                values_callable=lambda obj: [e.value for e in obj],
                name="productstatus",
                create_type=False,
            ),
            nullable=False,
            index=True,
            server_default="draft",
        ),
        description="draft | active | inactive",
    )

    version: int = Field(
        default=1,
        description="Incremented on significant configuration changes.",
    )

    template_id: Optional[_uuid.UUID] = Field(
        default=None,
        foreign_key="tenant_templates.id",
        index=True,
        description="The single TenantTemplate defining the KYC requirements for this product.",
    )
