"""Tenant Template service — CRUD operations for tenant-owned templates.

Tenant templates extend the active baseline definition identified by
`(template_type, baseline_level)`.
"""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.tenant.template import (
    TenantTemplate,
    TenantTemplateDefinition,
    TenantTemplateDefinitionReview,
    QuestionGroup,
    Question,
    QuestionOption,
)
from app.models.public.baseline_template import (
    BaselineTemplate,
    BaselineTemplateDefinition,
    BaselineQuestionGroup,
    BaselineQuestion,
    BaselineQuestionOption,
)
from app.models.enums import DefinitionReviewAction, DefinitionReviewStatus, TemplateType
from app.core.context import get_current_user
from app.schemas.tenant_templates import (
    TenantTemplateCreate,
    TenantTemplateUpdate,
    TenantTemplateDefinitionCreate,
    TenantTemplateDefinitionReviewRequest,
    TenantTemplateDefinitionUpdate,
)
from app.schemas.templates.form_schema import (
    QuestionGroupCreate,
    QuestionCreate,
    QuestionOptionCreate,
)


# ── Utils ─────────────────────────────────────────────────────────────

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge two dicts — override wins on conflicts."""
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


async def _load_version_with_groups(
    version_id: UUID,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    result = await session.execute(
        select(TenantTemplateDefinition)
        .where(TenantTemplateDefinition.id == version_id)
        .options(
            selectinload(TenantTemplateDefinition.question_groups)
            .selectinload(QuestionGroup.questions)
            .selectinload(Question.options),
            selectinload(TenantTemplateDefinition.ungrouped_questions)
            .selectinload(Question.options),
            selectinload(TenantTemplateDefinition.reviews),
        )
    )
    return result.scalars().first()


async def _get_active_baseline_definition(
    template_type: TemplateType,
    baseline_level: int,
    session: AsyncSession,
) -> Optional[BaselineTemplateDefinition]:
    """Return the active published baseline definition for a type/level pair."""
    result = await session.execute(
        select(BaselineTemplate).where(
            BaselineTemplate.template_type == template_type,
            BaselineTemplate.level == baseline_level,
            BaselineTemplate.is_active == True,
        )
    )
    baseline = result.scalars().first()

    if not baseline or not baseline.active_version_id:
        return None

    result = await session.execute(
        select(BaselineTemplateDefinition)
        .where(BaselineTemplateDefinition.id == baseline.active_version_id)
        .options(
            selectinload(BaselineTemplateDefinition.question_groups)
            .selectinload(BaselineQuestionGroup.questions)
            .selectinload(BaselineQuestion.options),
            selectinload(BaselineTemplateDefinition.ungrouped_questions)
            .selectinload(BaselineQuestion.options),
        )
    )
    return result.scalars().first()


def _copy_baseline_into_version(
    baseline_def: BaselineTemplateDefinition,
    version: TenantTemplateDefinition,
) -> None:
    """Copy all baseline groups/questions/options into the tenant version.

    Group rows: is_tenant_editable=True — tenants can append their own questions
      inside any copied baseline group.
    Question rows inside groups: is_tenant_editable=False — baseline questions
      are locked and cannot be modified or deleted by tenants.
    Ungrouped baseline questions: also copied with is_tenant_editable=False.
    """
    for src_group in baseline_def.question_groups:
        group = QuestionGroup(
            version=version,
            unique_key=src_group.unique_key,
            title=src_group.title,
            display_order=src_group.display_order,
            submit_api_url=src_group.submit_api_url,
            sequential_file_upload=src_group.sequential_file_upload,
            is_tenant_editable=True,  # group is open for tenant question additions
        )
        for src_q in src_group.questions:
            question = Question(
                group=group,
                unique_key=src_q.unique_key,
                label=src_q.label,
                field_type=src_q.field_type,
                required=src_q.required,
                display_order=src_q.display_order,
                regex=src_q.regex,
                keyboard_type=src_q.keyboard_type,
                min_date=src_q.min_date,
                max_date=src_q.max_date,
                depends_on_unique_key=src_q.depends_on_unique_key,
                visible_when_equals=src_q.visible_when_equals,
                rules=src_q.rules,
                is_tenant_editable=False,  # individual baseline questions are locked
            )
            for src_opt in src_q.options:
                QuestionOption(
                    question=question,
                    value=src_opt.value,
                    display_order=src_opt.display_order,
                    is_tenant_editable=False,
                )

    # Copy ungrouped questions directly attached to the baseline version
    for src_q in baseline_def.ungrouped_questions:
        question = Question(
            version=version,
            group_id=None,
            unique_key=src_q.unique_key,
            label=src_q.label,
            field_type=src_q.field_type,
            required=src_q.required,
            display_order=src_q.display_order,
            regex=src_q.regex,
            keyboard_type=src_q.keyboard_type,
            min_date=src_q.min_date,
            max_date=src_q.max_date,
            depends_on_unique_key=src_q.depends_on_unique_key,
            visible_when_equals=src_q.visible_when_equals,
            rules=src_q.rules,
            is_tenant_editable=False,
        )
        for src_opt in src_q.options:
            QuestionOption(
                question=question,
                value=src_opt.value,
                display_order=src_opt.display_order,
                is_tenant_editable=False,
            )


def _attach_tenant_groups(
    version: TenantTemplateDefinition,
    groups: List[QuestionGroupCreate],
    session: AsyncSession,
) -> None:
    """Attach tenant-provided question groups (is_tenant_editable=True)."""
    for g in groups:
        group = QuestionGroup(
            version=version,
            unique_key=g.unique_key,
            title=g.title,
            display_order=g.display_order,
            submit_api_url=g.submit_api_url,
            sequential_file_upload=g.sequential_file_upload,
            is_tenant_editable=True,
        )
        for q in g.questions:
            question = Question(
                group=group,
                unique_key=q.unique_key,
                label=q.label,
                field_type=q.field_type,
                required=q.required,
                display_order=q.display_order,
                regex=q.regex,
                keyboard_type=q.keyboard_type,
                min_date=q.min_date,
                max_date=q.max_date,
                depends_on_unique_key=q.depends_on_unique_key,
                visible_when_equals=q.visible_when_equals,
                rules=q.rules,
                is_tenant_editable=True,
            )
            for opt in q.options:
                QuestionOption(
                    question=question,
                    value=opt.value,
                    display_order=opt.display_order,
                    is_tenant_editable=True,
                )
            session.add(question)
        session.add(group)


def _attach_tenant_ungrouped_questions(
    version: TenantTemplateDefinition,
    questions: List[QuestionCreate],
    session: AsyncSession,
) -> None:
    """Attach tenant-provided groupless questions directly on the version."""
    for q in questions:
        question = Question(
            version=version,
            group_id=None,
            unique_key=q.unique_key,
            label=q.label,
            field_type=q.field_type,
            required=q.required,
            display_order=q.display_order,
            regex=q.regex,
            keyboard_type=q.keyboard_type,
            min_date=q.min_date,
            max_date=q.max_date,
            depends_on_unique_key=q.depends_on_unique_key,
            visible_when_equals=q.visible_when_equals,
            rules=q.rules,
            is_tenant_editable=True,
        )
        for opt in q.options:
            QuestionOption(
                question=question,
                value=opt.value,
                display_order=opt.display_order,
                is_tenant_editable=True,
            )
        session.add(question)


def _guard_tenant_editable(obj: Any, resource_name: str = "resource") -> None:
    """Raise 403 if the object is not tenant-editable."""
    if not obj.is_tenant_editable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot modify baseline-copied {resource_name}. "
                   f"It is read-only for tenants.",
        )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _reset_review_state(version: TenantTemplateDefinition) -> None:
    version.review_status = DefinitionReviewStatus.DRAFT
    version.submitted_for_review_at = None
    version.submitted_for_review_by = None
    version.reviewed_at = None
    version.reviewed_by = None
    version.review_notes = None


def _assert_definition_editable(version: TenantTemplateDefinition) -> None:
    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a published definition. Create a new version instead.",
        )
    if version.review_status == DefinitionReviewStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition is pending review and cannot be modified.",
        )


def _append_review_event(
    version: TenantTemplateDefinition,
    action: DefinitionReviewAction,
    *,
    notes: Optional[str] = None,
) -> TenantTemplateDefinitionReview:
    return TenantTemplateDefinitionReview(
        definition=version,
        action=action,
        notes=notes,
    )


# ── Tenant Template CRUD ──────────────────────────────────────────────

async def create_tenant_template(
    data: TenantTemplateCreate,
    session: AsyncSession,
) -> TenantTemplate:
    """Create a new tenant template.

    Validates that an active baseline exists for the requested type/level pair,
    then creates the header record.
    """
    baseline = await _get_active_baseline_definition(
        data.template_type,
        data.baseline_level,
        session,
    )
    if not baseline:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No active baseline found for "
                f"template_type '{data.template_type}' and level '{data.baseline_level}'. "
                "A published baseline must exist before creating a tenant template for that pair."
            ),
        )

    template = TenantTemplate(
        name=data.name,
        description=data.description,
        template_type=data.template_type,
        baseline_level=data.baseline_level,
    )

    if data.initial_version:
        version = await _create_version_with_baseline_copy(
            template=template,
            data=data.initial_version,
            baseline_def=baseline,
            session=session,
        )
        session.add(version)
        await session.flush()
        template.active_version_id = version.id

    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


async def _create_version_with_baseline_copy(
    template: TenantTemplate,
    data: TenantTemplateDefinitionCreate,
    baseline_def: BaselineTemplateDefinition,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    version = TenantTemplateDefinition(
        template=template,
        version_tag=data.version_tag,
        rules_config=data.rules_config,
        changelog=data.changelog,
        copied_from_baseline_version_id=baseline_def.id,
        is_draft=True,
    )
    _copy_baseline_into_version(baseline_def, version)
    if data.question_groups:
        _attach_tenant_groups(version, data.question_groups, session)
    if data.questions:
        _attach_tenant_ungrouped_questions(version, data.questions, session)
    return version


async def list_tenant_templates(session: AsyncSession) -> List[TenantTemplate]:
    """List all templates in the tenant's schema."""
    result = await session.exec(select(TenantTemplate).order_by(TenantTemplate.name))
    return list(result.all())


async def get_tenant_template(
    template_id: UUID,
    session: AsyncSession,
    include_versions: bool = False,
) -> TenantTemplate:
    """Get a tenant template by ID."""
    query = select(TenantTemplate).where(TenantTemplate.id == template_id)

    if include_versions:
        query = query.options(
            selectinload(TenantTemplate.versions)
            .selectinload(TenantTemplateDefinition.question_groups)
            .selectinload(QuestionGroup.questions)
            .selectinload(Question.options),
            selectinload(TenantTemplate.versions)
            .selectinload(TenantTemplateDefinition.ungrouped_questions)
            .selectinload(Question.options),
            selectinload(TenantTemplate.versions)
            .selectinload(TenantTemplateDefinition.reviews),
        )
        query = query.options(selectinload(TenantTemplate.versions))

    result = await session.exec(query)
    template = result.first()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant template not found.",
        )
    return template


async def get_tenant_template_with_config(
    template_id: UUID,
    session: AsyncSession,
) -> dict:
    """Return the active tenant template version with its question groups and rules.

    Returns a dict with:
      - template: TenantTemplate
      - version: TenantTemplateDefinition | None
      - question_groups: list[QuestionGroup]
      - ungrouped_questions: list[Question]
      - rules_config: dict
      - baseline_version: dict | None  (id, version_tag)
    """
    template = await get_tenant_template(template_id, session, include_versions=False)

    if not template.active_version_id:
        return {
            "template": template,
            "version": None,
            "question_groups": [],
            "ungrouped_questions": [],
            "rules_config": {},
            "baseline_version": None,
        }

    version = await _load_version_with_groups(template.active_version_id, session)
    if not version:
        return {
            "template": template,
            "version": None,
            "question_groups": [],
            "ungrouped_questions": [],
            "rules_config": {},
            "baseline_version": None,
        }

    config = await get_tenant_template_definition_with_config(version.id, session)

    return {
        "template": template,
        "version": version,
        "question_groups": config.get("question_groups", []),
        "ungrouped_questions": config.get("ungrouped_questions", []),
        "rules_config": config.get("rules_config", {}),
        "baseline_version": config.get("baseline_version"),
    }


async def get_tenant_template_definition_with_config(
    version_id: UUID,
    session: AsyncSession,
) -> dict:
    """Return a historical tenant template definition with merged config."""
    version = await _load_version_with_groups(version_id, session)
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant template definition not found.",
        )

    baseline_version_info: Optional[dict] = None
    baseline_rules_config: Dict[str, Any] = {}
    if version.copied_from_baseline_version_id:
        result = await session.execute(
            select(BaselineTemplateDefinition).where(
                BaselineTemplateDefinition.id == version.copied_from_baseline_version_id
            )
        )
        baseline_version = result.scalars().first()
        if baseline_version:
            baseline_version_info = {
                "id": baseline_version.id,
                "version_tag": baseline_version.version_tag,
            }
            baseline_rules_config = baseline_version.rules_config or {}

    return {
        "version": version,
        "question_groups": version.question_groups,
        "ungrouped_questions": version.ungrouped_questions,
        "rules_config": _deep_merge(baseline_rules_config, version.rules_config or {}),
        "baseline_version": baseline_version_info,
    }



async def update_tenant_template(
    template_id: UUID,
    data: TenantTemplateUpdate,
    session: AsyncSession,
) -> TenantTemplate:
    """Update a tenant template header."""
    template = await get_tenant_template(template_id, session)
    updates = data.model_dump(exclude_unset=True)

    if "active_version_id" in updates and updates["active_version_id"]:
        version = await get_tenant_template_definition(
            template_id, updates["active_version_id"], session
        )
        if version.is_draft:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot set a draft version as active. Publish it first.",
            )

    for key, value in updates.items():
        setattr(template, key, value)

    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


async def delete_tenant_template(template_id: UUID, session: AsyncSession) -> None:
    """Delete a tenant template and all its definitions."""
    template = await get_tenant_template(template_id, session)
    await session.delete(template)
    await session.commit()


# ── Definition Management ─────────────────────────────────────────────

async def create_tenant_template_definition(
    template_id: UUID,
    data: TenantTemplateDefinitionCreate,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Create a new version for a tenant template.

    Always copies questions from the template's baseline type's active version.
    Tenant-provided question_groups are appended after the copied baseline rows.
    """
    template = await get_tenant_template(template_id, session)
    baseline_def = await _get_active_baseline_definition(
        template.template_type,
        template.baseline_level,
        session,
    )

    if not baseline_def:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No published baseline version found for "
                f"template_type '{template.template_type}' and level '{template.baseline_level}'. "
                "Publish a baseline version before creating a new tenant version."
            ),
        )

    version = await _create_version_with_baseline_copy(
        template=template,
        data=data,
        baseline_def=baseline_def,
        session=session,
    )
    session.add(version)
    await session.commit()
    return await _load_version_with_groups(version.id, session)


async def get_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Get a specific definition of a tenant template (with nested rows)."""
    version = await _load_version_with_groups(version_id, session)
    if not version or version.template_id != template_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant template definition not found.",
        )
    return version


async def update_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionUpdate,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Update a tenant template definition (draft only)."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    _assert_definition_editable(version)

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(version, key, value)
    if updates and version.review_status in {
        DefinitionReviewStatus.APPROVED,
        DefinitionReviewStatus.CHANGES_REQUESTED,
    }:
        _reset_review_state(version)

    session.add(version)
    await session.commit()
    await session.refresh(version)
    return await _load_version_with_groups(version.id, session)


async def delete_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a draft tenant template definition."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    template = await get_tenant_template(template_id, session)
    _assert_definition_editable(version)

    if template.active_version_id == version.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the active version. Set a different active version first.",
        )

    await session.delete(version)
    await session.commit()


async def publish_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
    set_as_active: bool = True,
    auto_generate_transform_rules: bool = True,
) -> TenantTemplateDefinition:
    """Publish a draft tenant template definition.

    Validates that all unique_key values across groups in this version are
    unique before publishing.

    If auto_generate_transform_rules=True (default) and there is a previous
    active version, a draft TransformRuleSet is automatically generated by
    diffing the previous active version against the newly published one.
    The caller is responsible for reviewing and publishing that rule set
    before applying it to in-flight submissions.
    """
    version = await _load_version_with_groups(version_id, session)

    if not version or version.template_id != template_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant template definition not found.",
        )
    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition is already published.",
        )
    if version.review_status != DefinitionReviewStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition must be approved before it can be published.",
        )

    # Unique-key collision check across all groups
    seen_keys: set = set()
    for group in version.question_groups:
        if group.unique_key in seen_keys:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate group unique_key '{group.unique_key}' found in this version.",
            )
        seen_keys.add(group.unique_key)

        q_keys: set = set()
        for q in group.questions:
            if q.unique_key in q_keys:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Duplicate question unique_key '{q.unique_key}' in group '{group.unique_key}'.",
                )
            q_keys.add(q.unique_key)

    # Capture the previous active version ID before updating
    template = await get_tenant_template(template_id, session)
    previous_active_version_id: Optional[UUID] = template.active_version_id

    version.is_draft = False
    session.add(version)

    if set_as_active:
        template = await get_tenant_template(template_id, session)
        template.active_version_id = version.id
        session.add(template)

    await session.commit()
    await session.refresh(version)

    if auto_generate_transform_rules and previous_active_version_id:
        try:
            from app.services.transforms.diff_service import generate_rule_set
            await generate_rule_set(
                template_id=template_id,
                source_version_id=previous_active_version_id,
                target_version_id=version.id,
                changelog=(
                    f"Auto-generated on publish of version '{version.version_tag}'. "
                    "Review all rules before publishing this rule set."
                ),
                session=session,
            )
        except Exception:  # noqa: BLE001
            pass

    return await _load_version_with_groups(version.id, session)


async def submit_tenant_template_definition_for_review(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Move a draft tenant template definition into pending review."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    _assert_definition_editable(version)

    if version.review_status == DefinitionReviewStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition is already pending review.",
        )
    if version.review_status == DefinitionReviewStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition is already approved.",
        )

    version.review_status = DefinitionReviewStatus.PENDING_REVIEW
    version.submitted_for_review_at = _now_utc()
    version.submitted_for_review_by = get_current_user()
    version.reviewed_at = None
    version.reviewed_by = None
    version.review_notes = None
    session.add(version)
    session.add(
        _append_review_event(
            version,
            DefinitionReviewAction.SUBMITTED,
            notes=data.notes,
        )
    )
    await session.commit()
    return await _load_version_with_groups(version.id, session)


async def approve_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Approve a pending tenant template definition as super_admin."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft definitions can be approved.",
        )
    if version.review_status != DefinitionReviewStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition must be pending review before approval.",
        )
    reviewer = get_current_user()
    if version.submitted_for_review_by and reviewer == version.submitted_for_review_by:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Submitter cannot approve their own definition.",
        )

    version.review_status = DefinitionReviewStatus.APPROVED
    version.reviewed_at = _now_utc()
    version.reviewed_by = reviewer
    version.review_notes = data.notes
    session.add(version)
    session.add(
        _append_review_event(
            version,
            DefinitionReviewAction.APPROVED,
            notes=data.notes,
        )
    )
    await session.commit()
    return await _load_version_with_groups(version.id, session)


async def request_changes_tenant_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: TenantTemplateDefinitionReviewRequest,
    session: AsyncSession,
) -> TenantTemplateDefinition:
    """Return a pending definition to the tenant with changes requested."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft definitions can receive review feedback.",
        )
    if version.review_status != DefinitionReviewStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition must be pending review before requesting changes.",
        )

    version.review_status = DefinitionReviewStatus.CHANGES_REQUESTED
    version.reviewed_at = _now_utc()
    version.reviewed_by = get_current_user()
    version.review_notes = data.notes
    session.add(version)
    session.add(
        _append_review_event(
            version,
            DefinitionReviewAction.CHANGES_REQUESTED,
            notes=data.notes,
        )
    )
    await session.commit()
    return await _load_version_with_groups(version.id, session)


# ── Question Group CRUD (tenant-editable only) ────────────────────────

async def add_question_group(
    template_id: UUID,
    version_id: UUID,
    data: QuestionGroupCreate,
    session: AsyncSession,
) -> QuestionGroup:
    """Add a tenant-owned question group to a draft version."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    _assert_draft(version)

    group = QuestionGroup(
        version_id=version.id,
        unique_key=data.unique_key,
        title=data.title,
        display_order=data.display_order,
        submit_api_url=data.submit_api_url,
        sequential_file_upload=data.sequential_file_upload,
        is_tenant_editable=True,
    )
    for q in data.questions:
        _build_question(group, q, session)

    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def delete_question_group(group_id: UUID, session: AsyncSession) -> None:
    """Delete a tenant-owned question group from a draft version."""
    group = await session.get(QuestionGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    _guard_tenant_editable(group, "question group")

    version = await session.get(TenantTemplateDefinition, group.version_id)
    _assert_draft(version)

    await session.delete(group)
    await session.commit()


# ── Question CRUD (tenant-editable only) ──────────────────────────────

async def add_question(
    group_id: UUID,
    data: QuestionCreate,
    session: AsyncSession,
) -> Question:
    """Add a tenant-owned question to a draft group."""
    group = await session.get(QuestionGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    _guard_tenant_editable(group, "question group")

    version = await session.get(TenantTemplateDefinition, group.version_id)
    _assert_draft(version)

    question = _build_question(group, data, session)
    await session.commit()
    await session.refresh(question)
    return question


async def add_ungrouped_question(
    template_id: UUID,
    version_id: UUID,
    data: QuestionCreate,
    session: AsyncSession,
) -> Question:
    """Add a tenant-owned question directly to a version (no group)."""
    version = await get_tenant_template_definition(template_id, version_id, session)
    _assert_draft(version)

    question = Question(
        version=version,
        group_id=None,
        unique_key=data.unique_key,
        label=data.label,
        field_type=data.field_type,
        required=data.required,
        display_order=data.display_order,
        regex=data.regex,
        keyboard_type=data.keyboard_type,
        min_date=data.min_date,
        max_date=data.max_date,
        depends_on_unique_key=data.depends_on_unique_key,
        visible_when_equals=data.visible_when_equals,
        rules=data.rules,
        is_tenant_editable=True,
    )
    for opt in data.options:
        QuestionOption(
            question=question,
            value=opt.value,
            display_order=opt.display_order,
            is_tenant_editable=True,
        )
    session.add(question)
    await session.commit()
    await session.refresh(question)
    return question


async def delete_question(question_id: UUID, session: AsyncSession) -> None:
    """Delete a tenant-owned question (grouped or ungrouped)."""
    question = await session.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")
    _guard_tenant_editable(question, "question")

    if question.group_id:
        group = await session.get(QuestionGroup, question.group_id)
        version = await session.get(TenantTemplateDefinition, group.version_id)
    else:
        version = await session.get(TenantTemplateDefinition, question.version_id)
    _assert_draft(version)

    await session.delete(question)
    await session.commit()


# ── Internal helpers ──────────────────────────────────────────────────

def _assert_draft(version: TenantTemplateDefinition) -> None:
    if not version or not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a published template definition.",
        )


def _build_question(
    group: QuestionGroup,
    data: QuestionCreate,
    session: AsyncSession,
) -> Question:
    question = Question(
        group=group,
        unique_key=data.unique_key,
        label=data.label,
        field_type=data.field_type,
        required=data.required,
        display_order=data.display_order,
        regex=data.regex,
        keyboard_type=data.keyboard_type,
        min_date=data.min_date,
        max_date=data.max_date,
        depends_on_unique_key=data.depends_on_unique_key,
        visible_when_equals=data.visible_when_equals,
        rules=data.rules,
        is_tenant_editable=True,
    )
    for opt in data.options:
        QuestionOption(
            question=question,
            value=opt.value,
            display_order=opt.display_order,
            is_tenant_editable=True,
        )
    session.add(question)
    return question
