"""Baseline Template routes — system-owned templates in public schema.

These routes provide:
- Read access for all authenticated users (tenants can view baselines)
- Write access for system administrators only

Security Note:
The mutating endpoints (POST, PATCH, DELETE) should be protected by
admin authentication middleware. This is not implemented here - add
appropriate middleware or dependency injection for your auth system.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_role
from app.core.authz import require_permission
from app.db.session import get_public_session
from app.models.enums import TemplateType
from app.schemas.baseline_templates import (
    BaselineTemplateCreate,
    BaselineTemplateRead,
    BaselineTemplateUpdate,
    BaselineTemplateReadWithVersions,
    BaselineTemplateDefinitionCreate,
    BaselineTemplateDefinitionRead,
    BaselineTemplateDefinitionUpdate,
)
from app.services import baseline_templates as baseline_svc

router = APIRouter(
    prefix="/baseline-templates",
    tags=["baseline-templates"],
    dependencies=[Depends(require_role())],
)


# ── Template CRUD ─────────────────────────────────────────────────────

@router.post("", response_model=BaselineTemplateRead, status_code=201)
async def create_baseline_template(
    data: BaselineTemplateCreate,
    _auth=Depends(require_permission("baseline_templates.create")),
    session: AsyncSession = Depends(get_public_session),
):
    """Create a new baseline template (admin only).
    
    Optionally include an initial_version to create the first
    definition alongside the template.
    """
    return await baseline_svc.create_baseline_template(data, session)


@router.get("", response_model=list[BaselineTemplateRead])
async def list_baseline_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    template_type: Optional[TemplateType] = Query(None, description="Filter by baseline type"),
    level: Optional[int] = Query(None, ge=1, description="Filter by business level"),
    active_only: bool = Query(True, description="Only return active templates"),
    _auth=Depends(require_permission("baseline_templates.read")),
    session: AsyncSession = Depends(get_public_session),
):
    """List all baseline templates.
    
    Available to all authenticated users (tenants and admins).
    """
    return await baseline_svc.list_baseline_templates(
        session,
        category=category,
        template_type=template_type,
        level=level,
        active_only=active_only,
    )


@router.get("/{template_id}", response_model=BaselineTemplateReadWithVersions)
async def get_baseline_template(
    template_id: UUID,
    _auth=Depends(require_permission("baseline_templates.read")),
    session: AsyncSession = Depends(get_public_session),
):
    """Get a baseline template by ID with all its versions.
    
    Available to all authenticated users.
    """
    return await baseline_svc.get_baseline_template(
        template_id, session, include_versions=True
    )


@router.patch(
    "/{template_id}",
    response_model=BaselineTemplateRead,
)
async def update_baseline_template(
    template_id: UUID,
    data: BaselineTemplateUpdate,
    _auth=Depends(require_permission("baseline_templates.update")),
    session: AsyncSession = Depends(get_public_session),
):
    """Update a baseline template (admin only).
    
    Cannot update locked templates.
    """
    return await baseline_svc.update_baseline_template(template_id, data, session)


@router.delete(
    "/{template_id}",
    status_code=204,
)
async def delete_baseline_template(
    template_id: UUID,
    force: bool = Query(False, description="Force delete even if tenants extend it"),
    _auth=Depends(require_permission("baseline_templates.delete")),
    session: AsyncSession = Depends(get_public_session),
):
    """Delete a baseline template (admin only).
    
    Cannot delete locked templates.
    """
    await baseline_svc.delete_baseline_template(template_id, session, force=force)


# ── Definition CRUD ───────────────────────────────────────────────────

@router.post(
    "/{template_id}/definitions",
    response_model=BaselineTemplateDefinitionRead,
    status_code=201,
)
async def create_baseline_template_definition(
    template_id: UUID,
    data: BaselineTemplateDefinitionCreate,
    _auth=Depends(require_permission("baseline_templates.definitions.create")),
    session: AsyncSession = Depends(get_public_session),
):
    """Create a new version/definition for a baseline template (admin only).
    
    New definitions start as drafts and must be published before use.
    """
    return await baseline_svc.create_baseline_template_definition(
        template_id, data, session
    )


@router.get(
    "/{template_id}/definitions/{version_id}",
    response_model=BaselineTemplateDefinitionRead,
)
async def get_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    _auth=Depends(require_permission("baseline_templates.read")),
    session: AsyncSession = Depends(get_public_session),
):
    """Get a specific version/definition of a baseline template."""
    return await baseline_svc.get_baseline_template_definition(
        template_id, version_id, session
    )


@router.patch(
    "/{template_id}/definitions/{version_id}",
    response_model=BaselineTemplateDefinitionRead,
)
async def update_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: BaselineTemplateDefinitionUpdate,
    _auth=Depends(require_permission("baseline_templates.definitions.update")),
    session: AsyncSession = Depends(get_public_session),
):
    """Update a baseline template definition (admin only, draft only).
    
    Published definitions cannot be updated - create a new version instead.
    """
    return await baseline_svc.update_baseline_template_definition(
        template_id, version_id, data, session
    )


@router.delete(
    "/{template_id}/definitions/{version_id}",
    status_code=204,
)
async def delete_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    _auth=Depends(require_permission("baseline_templates.definitions.delete")),
    session: AsyncSession = Depends(get_public_session),
):
    """Delete a baseline template definition (admin only).
    
    Cannot delete the active version.
    """
    await baseline_svc.delete_baseline_template_definition(
        template_id, version_id, session
    )


@router.post(
    "/{template_id}/definitions/{version_id}/publish",
    response_model=BaselineTemplateDefinitionRead,
)
async def publish_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    set_as_active: bool = Query(True, description="Set this version as active"),
    _auth=Depends(require_permission("baseline_templates.publish")),
    session: AsyncSession = Depends(get_public_session),
):
    """Publish a draft baseline template definition (admin only).
    
    Publishing makes the definition immutable. Optionally sets it as
    the active version for the template.
    """
    return await baseline_svc.publish_baseline_template_definition(
        template_id, version_id, session, set_as_active=set_as_active
    )
