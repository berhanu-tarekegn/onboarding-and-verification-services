"""Tenant Template routes — tenant-owned templates in per-tenant schemas.

These routes are scoped to the current tenant (via X-Tenant-ID header).
The session automatically uses the correct PostgreSQL schema via search_path.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_role
from app.core.dependencies import require_tenant_header
from app.db.session import tenant_session_for_permissions
from app.schemas.tenant_templates import (
    TenantTemplateCreate,
    TenantTemplateRead,
    TenantTemplateUpdate,
    TenantTemplateReadWithVersions,
    TenantTemplateReadWithConfig,
    TenantTemplateDefinitionCreate,
    TenantTemplateDefinitionRead,
    TenantTemplateDefinitionReviewRequest,
    TenantTemplateDefinitionUpdate,
)
from app.schemas.templates.form_schema import (
    QuestionCreate,
    QuestionGroupCreate,
    QuestionGroupRead,
    QuestionRead,
)
from app.services import tenant_templates as tenant_template_svc

router = APIRouter(
    prefix="/templates",
    tags=["tenant-templates"],
    dependencies=[Depends(require_tenant_header)],
)


# ── Template CRUD ─────────────────────────────────────────────────────

@router.post("", response_model=TenantTemplateRead, status_code=201)
async def create_tenant_template(
    data: TenantTemplateCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.create")),
):
    """Create a new template for the current tenant.

    template_type and baseline_level must match an existing active BaselineTemplate.
    Mandatory baseline questions for that type are copied automatically.
    """
    return await tenant_template_svc.create_tenant_template(data, session)


@router.get("", response_model=list[TenantTemplateRead])
async def list_tenant_templates(
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
):
    """List all templates for the current tenant."""
    return await tenant_template_svc.list_tenant_templates(session)


@router.get("/{template_id}", response_model=TenantTemplateReadWithVersions)
async def get_tenant_template(
    template_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
):
    """Get a tenant template by ID with all its versions."""
    return await tenant_template_svc.get_tenant_template(
        template_id, session, include_versions=True
    )


@router.patch("/{template_id}", response_model=TenantTemplateRead)
async def update_tenant_template(
    template_id: UUID,
    data: TenantTemplateUpdate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Update a tenant template."""
    return await tenant_template_svc.update_tenant_template(template_id, data, session)


@router.delete("/{template_id}", status_code=204)
async def delete_tenant_template(
    template_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.delete")),
):
    """Delete a tenant template and all its definitions."""
    await tenant_template_svc.delete_tenant_template(template_id, session)


# ── Definition CRUD ───────────────────────────────────────────────────

@router.post(
    "/{template_id}/definitions",
    response_model=TenantTemplateDefinitionRead,
    status_code=201,
)
async def create_tenant_template_definition(
    template_id: UUID,
    data: TenantTemplateDefinitionCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Create a new version/definition for a tenant template.

    Automatically copies mandatory baseline questions for the template's type/level.
    New definitions start as drafts.
    """
    return await tenant_template_svc.create_tenant_template_definition(
        template_id, data, session
    )


@router.get(
    "/{template_id}/definitions/{version_id}",
    response_model=TenantTemplateDefinitionRead,
)
async def get_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
):
    """Get a specific version/definition of a tenant template."""
    return await tenant_template_svc.get_tenant_template_definition(
        template_id, version_id, session
    )


@router.patch(
    "/{template_id}/definitions/{version_id}",
    response_model=TenantTemplateDefinitionRead,
)
async def update_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionUpdate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Update a tenant template definition (draft only)."""
    return await tenant_template_svc.update_tenant_template_definition(
        template_id, version_id, data, session
    )


@router.delete("/{template_id}/definitions/{version_id}", status_code=204)
async def delete_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Delete a draft tenant template definition."""
    await tenant_template_svc.delete_tenant_template_definition(
        template_id, version_id, session
    )


@router.post(
    "/{template_id}/definitions/{version_id}/publish",
    response_model=TenantTemplateDefinitionRead,
)
async def publish_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    set_as_active: bool = Query(True, description="Set this version as active"),
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.publish")),
):
    """Publish a draft tenant template definition.

    Validates unique_key uniqueness across all groups before publishing.
    """
    return await tenant_template_svc.publish_tenant_template_definition(
        template_id, version_id, session, set_as_active=set_as_active
    )


@router.post(
    "/{template_id}/definitions/{version_id}/submit-review",
    response_model=TenantTemplateDefinitionRead,
)
async def submit_tenant_template_definition_for_review(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Submit a draft tenant template definition for super-admin review."""
    return await tenant_template_svc.submit_tenant_template_definition_for_review(
        template_id, version_id, data, session
    )


@router.post(
    "/{template_id}/definitions/{version_id}/approve",
    response_model=TenantTemplateDefinitionRead,
)
async def approve_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    _ctx=Depends(require_role("super_admin")),
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
):
    """Approve a pending tenant template definition as super_admin."""
    return await tenant_template_svc.approve_tenant_template_definition(
        template_id, version_id, data, session
    )


@router.post(
    "/{template_id}/definitions/{version_id}/request-changes",
    response_model=TenantTemplateDefinitionRead,
)
async def request_changes_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    _ctx=Depends(require_role("super_admin")),
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.read")),
):
    """Request changes on a pending tenant template definition as super_admin."""
    return await tenant_template_svc.request_changes_tenant_template_definition(
        template_id, version_id, data, session
    )


@router.post(
    "/{template_id}/definitions/{version_id}/groups",
    response_model=QuestionGroupRead,
    status_code=201,
)
async def add_tenant_template_question_group(
    template_id: UUID,
    version_id: UUID,
    data: QuestionGroupCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Add a tenant-owned question group to a draft template definition."""
    return await tenant_template_svc.add_question_group(
        template_id, version_id, data, session
    )


@router.delete(
    "/{template_id}/definitions/{version_id}/groups/{group_id}",
    status_code=204,
)
async def delete_tenant_template_question_group(
    template_id: UUID,
    version_id: UUID,
    group_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Delete a tenant-owned question group from a draft template definition."""
    _ = template_id
    _ = version_id
    await tenant_template_svc.delete_question_group(group_id, session)


@router.post(
    "/{template_id}/definitions/{version_id}/groups/{group_id}/questions",
    response_model=QuestionRead,
    status_code=201,
)
async def add_tenant_template_group_question(
    template_id: UUID,
    version_id: UUID,
    group_id: UUID,
    data: QuestionCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Add a tenant-owned question to a tenant-editable draft group."""
    _ = template_id
    _ = version_id
    return await tenant_template_svc.add_question(group_id, data, session)


@router.post(
    "/{template_id}/definitions/{version_id}/questions",
    response_model=QuestionRead,
    status_code=201,
)
async def add_tenant_template_ungrouped_question(
    template_id: UUID,
    version_id: UUID,
    data: QuestionCreate,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Add a tenant-owned ungrouped question directly to a draft definition."""
    return await tenant_template_svc.add_ungrouped_question(
        template_id, version_id, data, session
    )


@router.delete(
    "/{template_id}/definitions/{version_id}/questions/{question_id}",
    status_code=204,
)
async def delete_tenant_template_question(
    template_id: UUID,
    version_id: UUID,
    question_id: UUID,
    session: AsyncSession = Depends(tenant_session_for_permissions("templates.update")),
):
    """Delete a tenant-owned grouped or ungrouped question from a draft definition."""
    _ = template_id
    _ = version_id
    await tenant_template_svc.delete_question(question_id, session)
