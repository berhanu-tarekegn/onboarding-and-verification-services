"""Product API schemas — request/response models for products and KYC config."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import SQLModel, Field

from app.models.tenant.product import ProductStatus
from app.schemas.templates.form_schema import QuestionGroupRead


# ── Product Schemas ───────────────────────────────────────────────────

class ProductBase(SQLModel):
    """Base fields for products."""

    name: str = Field(max_length=255)
    description: Optional[str] = None


class ProductCreate(ProductBase):
    """Request body for creating a new product.

    template_id is optional at creation — the product starts in DRAFT and a
    template can be assigned later via PATCH before activation.
    """

    product_code: str = Field(max_length=100)
    template_id: Optional[UUID] = Field(
        default=None,
        description="The TenantTemplate that defines the KYC requirements for this product.",
    )


class ProductUpdate(SQLModel):
    """Request body for updating a product.

    product_code updates are blocked when the product is ACTIVE.
    template_id can be changed while the product is in DRAFT or INACTIVE.
    """

    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    product_code: Optional[str] = Field(default=None, max_length=100)
    template_id: Optional[UUID] = Field(
        default=None,
        description="Assign or reassign the KYC template for this product.",
    )


class ProductRead(ProductBase):
    """Response model for a product."""

    id: UUID
    product_code: str
    status: ProductStatus
    version: int
    template_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


# ── KYC Config Resolution ─────────────────────────────────────────────

class ProductKycConfigRead(SQLModel):
    """Response model for GET /products/{id}/kyc-config.

    Returns the product metadata plus the fully resolved KYC question groups
    of its linked template (baseline questions + tenant additions merged).
    """

    product: ProductRead
    template_id: UUID
    template_name: str
    question_groups: List[QuestionGroupRead] = Field(default_factory=list)
    rules_config: Dict[str, Any] = {}
    baseline_version_id: Optional[UUID] = None
    baseline_version_tag: Optional[str] = None
