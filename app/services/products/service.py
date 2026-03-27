"""Product service — CRUD, lifecycle, and KYC config resolution.

Business rules:
- product_code is unique per tenant schema (enforced by DB; caught as IntegrityError)
- Products are created in DRAFT status with version=1
- Each product has exactly one KYC template (template_id on the Product row itself)
- template_id is optional at creation but required before activation
- template_id can only be changed while the product is DRAFT or INACTIVE
- product_code changes are blocked while the product is ACTIVE
- Deleting an ACTIVE product is blocked (deactivate first)
- The linked TenantTemplate must be active (is_active=True) when assigned
    - KYC config resolution reuses get_tenant_template_with_config from the
  tenant_templates service — no duplication of merge logic
"""

from uuid import UUID
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import exc as sa_exc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.tenant.product import Product, ProductStatus
from app.models.tenant.template import TenantTemplate
from app.schemas.products import (
    ProductCreate,
    ProductUpdate,
)
import app.services.tenant_templates as template_svc


# ── Internal helpers ──────────────────────────────────────────────────

async def _validate_template_active(
    template_id: UUID,
    session: AsyncSession,
) -> TenantTemplate:
    """Ensure the referenced TenantTemplate exists and is active."""
    result = await session.exec(
        select(TenantTemplate).where(
            TenantTemplate.id == template_id,
            TenantTemplate.is_active == True,
        )
    )
    template = result.first()
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found or is inactive.",
        )
    return template


# ── Product CRUD ──────────────────────────────────────────────────────

async def create_product(
    data: ProductCreate,
    session: AsyncSession,
) -> Product:
    """Create a new product in DRAFT status.

    template_id is optional at creation. If provided, the template is validated
    as active before the product is committed.
    """
    if data.template_id is not None:
        await _validate_template_active(data.template_id, session)

    product = Product(
        name=data.name,
        description=data.description,
        product_code=data.product_code,
        template_id=data.template_id,
        status=ProductStatus.DRAFT,
        version=1,
    )
    session.add(product)

    try:
        await session.commit()
    except sa_exc.IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A product with code '{data.product_code}' already exists for this tenant.",
        )

    await session.refresh(product)
    return product


async def list_products(
    session: AsyncSession,
    status_filter: Optional[ProductStatus] = None,
) -> List[Product]:
    """List all products in the tenant schema, with optional status filter."""
    query = select(Product)
    if status_filter is not None:
        query = query.where(Product.status == status_filter)
    query = query.order_by(Product.name)
    result = await session.exec(query)
    return list(result.all())


async def get_product(
    product_id: UUID,
    session: AsyncSession,
) -> Product:
    """Get a product by ID. Raises 404 if not found."""
    result = await session.exec(
        select(Product).where(Product.id == product_id)
    )
    product = result.first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )
    return product


async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    session: AsyncSession,
) -> Product:
    """Update a product.

    - product_code changes are blocked while ACTIVE
    - template_id changes are blocked while ACTIVE
    """
    product = await get_product(product_id, session)
    updates = data.model_dump(exclude_unset=True)

    if product.status == ProductStatus.ACTIVE:
        if "product_code" in updates:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot change product_code while the product is ACTIVE. Deactivate it first.",
            )
        if "template_id" in updates:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot change the KYC template while the product is ACTIVE. Deactivate it first.",
            )

    if "template_id" in updates and updates["template_id"] is not None:
        await _validate_template_active(updates["template_id"], session)

    for key, value in updates.items():
        setattr(product, key, value)

    session.add(product)
    try:
        await session.commit()
    except sa_exc.IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A product with code '{updates.get('product_code')}' already exists for this tenant.",
        )
    await session.refresh(product)
    return product


async def delete_product(
    product_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a product.

    Blocked if the product is ACTIVE. Deactivate it before deleting.
    """
    product = await get_product(product_id, session)
    if product.status == ProductStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete an ACTIVE product. Deactivate it first.",
        )
    await session.delete(product)
    await session.commit()


# ── Lifecycle ─────────────────────────────────────────────────────────

async def activate_product(
    product_id: UUID,
    session: AsyncSession,
) -> Product:
    """Activate a DRAFT or INACTIVE product.

    Requires a template_id to be set before activation, ensuring the product
    has a KYC configuration ready for onboarding sessions.
    """
    product = await get_product(product_id, session)

    if product.status == ProductStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Product is already ACTIVE.",
        )

    if product.template_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot activate a product without a KYC template. Assign a template first.",
        )

    product.status = ProductStatus.ACTIVE
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def deactivate_product(
    product_id: UUID,
    session: AsyncSession,
) -> Product:
    """Deactivate an ACTIVE product, transitioning it to INACTIVE."""
    product = await get_product(product_id, session)

    if product.status != ProductStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot deactivate a product with status '{product.status}'. Only ACTIVE products can be deactivated.",
        )

    product.status = ProductStatus.INACTIVE
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


# ── KYC Config Resolution ─────────────────────────────────────────────

async def get_product_kyc_config(
    product_id: UUID,
    session: AsyncSession,
) -> Dict[str, Any]:
    """Resolve and return the full KYC configuration for an active product.

    Calls get_tenant_template_with_config on the product's single linked template
    to return the question_groups, ungrouped_questions, and rules_config.

    Returns:
        {
            "product": Product,
            "template_id": UUID,
            "template_name": str,
            "question_groups": list[QuestionGroup],
            "ungrouped_questions": list[Question],
            "rules_config": dict,
            "baseline_version_id": UUID | None,
            "baseline_version_tag": str | None,
        }
    """
    product = await get_product(product_id, session)

    if product.status != ProductStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"KYC config is only available for ACTIVE products. Current status: '{product.status}'.",
        )

    config = await template_svc.get_tenant_template_with_config(
        product.template_id, session
    )
    template = config["template"]
    baseline_version = config.get("baseline_version")

    return {
        "product": product,
        "template_id": template.id,
        "template_name": template.name,
        "question_groups": config.get("question_groups", []),
        "ungrouped_questions": config.get("ungrouped_questions", []),
        "rules_config": config.get("rules_config", {}),
        "baseline_version_id": baseline_version["id"] if baseline_version else None,
        "baseline_version_tag": baseline_version["version_tag"] if baseline_version else None,
    }
