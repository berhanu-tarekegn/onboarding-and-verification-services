"""Product routes — product and KYC template management in per-tenant schemas.

All routes require X-Tenant-ID header. Schema isolation is enforced automatically
via get_tenant_session() which sets the PostgreSQL search_path to the tenant schema.

Route groups:
    /products                         → Product CRUD
    /products/{id}/activate           → Lifecycle transitions
    /products/{id}/deactivate         → Lifecycle transitions
    /products/{id}/kyc-config         → Resolved KYC config for onboarding

Each product has exactly one KYC template assigned via the template_id field.
The template can be set at creation or updated via PATCH while the product is
in DRAFT or INACTIVE status.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dependencies import require_tenant_header
from app.core.authz import enforce_write_columns
from app.db.session import tenant_session_for_permissions
from app.models.tenant.product import ProductStatus
from app.schemas.products import (
    ProductCreate,
    ProductUpdate,
    ProductRead,
    ProductKycConfigRead,
)
from app.services.products import service as product_svc

router = APIRouter(
    prefix="/products",
    tags=["products"],
    dependencies=[
        Depends(require_tenant_header),
    ],
)


# ── Product CRUD ──────────────────────────────────────────────────────

@router.post("", response_model=ProductRead, status_code=201)
async def create_product(
    data: ProductCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.create")),
):
    """Create a new product for the current tenant.

    The product is created in DRAFT status with version=1.
    Optionally accepts a template_id to assign the KYC template immediately.
    A template must be assigned before the product can be activated.
    """
    return await product_svc.create_product(data, session)


@router.get("", response_model=list[ProductRead])
async def list_products(
    status: Optional[ProductStatus] = Query(None, description="Filter by product status"),
    session: AsyncSession = Depends(tenant_session_for_permissions("products.read")),
):
    """List all products for the current tenant."""
    return await product_svc.list_products(session, status_filter=status)


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.read")),
):
    """Get a product by ID."""
    return await product_svc.get_product(product_id, session)


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    request: Request,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.update")),
):
    """Update a product.

    - Changing product_code or template_id is blocked while the product is ACTIVE.
    - To reassign the KYC template, deactivate the product first.
    """
    # Field-level write rules (policy-driven).
    updates = data.model_dump(exclude_unset=True)
    if updates:
        enforce_write_columns(request, "products.update", set(updates.keys()))
    return await product_svc.update_product(product_id, data, session)


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.delete")),
):
    """Delete a product. Blocked if the product is ACTIVE."""
    await product_svc.delete_product(product_id, session)


# ── Lifecycle ─────────────────────────────────────────────────────────

@router.post("/{product_id}/activate", response_model=ProductRead)
async def activate_product(
    product_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.activate")),
):
    """Activate a DRAFT or INACTIVE product.

    Requires a template_id to be set. Transitions status to ACTIVE,
    making the product available for onboarding sessions.
    """
    return await product_svc.activate_product(product_id, session)


@router.post("/{product_id}/deactivate", response_model=ProductRead)
async def deactivate_product(
    product_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.deactivate")),
):
    """Deactivate an ACTIVE product, transitioning it to INACTIVE."""
    return await product_svc.deactivate_product(product_id, session)


# ── KYC Config Resolution ─────────────────────────────────────────────

@router.get("/{product_id}/kyc-config", response_model=ProductKycConfigRead)
async def get_product_kyc_config(
    product_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("products.read")),
):
    """Get the fully resolved KYC configuration for an active product.

    Returns the product metadata plus its linked template's resolved
    form_schema and rules_config (baseline + tenant overrides merged).

    Only available for ACTIVE products.
    """
    result = await product_svc.get_product_kyc_config(product_id, session)
    return ProductKycConfigRead(
        product=result["product"],
        template_id=result["template_id"],
        template_name=result["template_name"],
        question_groups=result["question_groups"],
        rules_config=result["rules_config"],
        baseline_version_id=result["baseline_version_id"],
        baseline_version_tag=result["baseline_version_tag"],
    )
