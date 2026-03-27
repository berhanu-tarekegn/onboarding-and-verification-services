"""Baseline Template service — CRUD operations for system-owned templates.

Baseline templates are identified by `(template_type, level)` and each pair
owns an immutable version history.
"""

from uuid import UUID
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.public.baseline_template import (
    BaselineTemplate,
    BaselineTemplateDefinition,
    BaselineQuestionGroup,
    BaselineQuestion,
    BaselineQuestionOption,
)
from app.models.enums import TemplateType
from app.schemas.baseline_templates import (
    BaselineTemplateCreate,
    BaselineTemplateUpdate,
    BaselineTemplateDefinitionCreate,
    BaselineTemplateDefinitionUpdate,
)
from app.schemas.templates.form_schema import (
    QuestionGroupCreate,
    QuestionCreate,
    QuestionOptionCreate,
)


# ── Private helpers ───────────────────────────────────────────────────

async def _load_definition_with_groups(
    version_id: UUID,
    session: AsyncSession,
) -> BaselineTemplateDefinition:
    """Load a definition with all nested question_groups/questions/options plus ungrouped questions."""
    result = await session.execute(
        select(BaselineTemplateDefinition)
        .where(BaselineTemplateDefinition.id == version_id)
        .options(
            selectinload(BaselineTemplateDefinition.question_groups)
            .selectinload(BaselineQuestionGroup.questions)
            .selectinload(BaselineQuestion.options),
            selectinload(BaselineTemplateDefinition.ungrouped_questions)
            .selectinload(BaselineQuestion.options),
        )
    )
    version = result.scalars().first()
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Baseline template definition not found.",
        )
    return version


def _copy_groups_from_definition(
    source: BaselineTemplateDefinition,
    target: BaselineTemplateDefinition,
) -> None:
    """Copy all question groups/questions/options from source → target definition.

    Also copies any ungrouped questions (group_id=None, version_id set) that
    are directly attached to the source version.
    """
    for src_group in source.question_groups:
        new_group = BaselineQuestionGroup(
            version_id=target.id,
            unique_key=src_group.unique_key,
            title=src_group.title,
            display_order=src_group.display_order,
            submit_api_url=src_group.submit_api_url,
            sequential_file_upload=src_group.sequential_file_upload,
        )
        for src_q in src_group.questions:
            new_q = BaselineQuestion(
                group=new_group,
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
            )
            for src_opt in src_q.options:
                BaselineQuestionOption(
                    question=new_q,
                    value=src_opt.value,
                    display_order=src_opt.display_order,
                )
        target.question_groups.append(new_group)

    # Copy ungrouped questions attached directly to the version
    for src_q in source.ungrouped_questions:
        new_q = BaselineQuestion(
            version=target,
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
        )
        for src_opt in src_q.options:
            BaselineQuestionOption(
                question=new_q,
                value=src_opt.value,
                display_order=src_opt.display_order,
            )


# ── Baseline Template CRUD ────────────────────────────────────────────

async def create_baseline_template(
    data: BaselineTemplateCreate,
    session: AsyncSession,
) -> BaselineTemplate:
    """Create a new baseline template in the public schema (admin-only)."""
    template_data = data.model_dump(exclude={"initial_version"})
    template = BaselineTemplate(**template_data)

    if data.initial_version:
        version = BaselineTemplateDefinition(
            template=template,
            version_tag=data.initial_version.version_tag,
            rules_config=data.initial_version.rules_config,
            changelog=data.initial_version.changelog,
            is_draft=True,
        )
        _attach_question_groups_from_payload(version, data.initial_version.question_groups, session)
        _attach_ungrouped_questions_from_payload(version, data.initial_version.questions, session)
        session.add(version)
        await session.flush()
        template.active_version_id = version.id

    session.add(template)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A baseline template already exists for "
                f"type '{data.template_type}' and level '{data.level}'."
            ),
        )

    await session.refresh(template)
    return template


def _attach_question_groups_from_payload(
    version: BaselineTemplateDefinition,
    groups: List[QuestionGroupCreate],
    session: AsyncSession,
) -> None:
    """Build and attach BaselineQuestionGroup rows from the create payload."""
    for g in groups:
        group = BaselineQuestionGroup(
            version=version,
            unique_key=g.unique_key,
            title=g.title,
            display_order=g.display_order,
            submit_api_url=g.submit_api_url,
            sequential_file_upload=g.sequential_file_upload,
        )
        for q in g.questions:
            question = BaselineQuestion(
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
            )
            for opt in q.options:
                BaselineQuestionOption(
                    question=question,
                    value=opt.value,
                    display_order=opt.display_order,
                )
            session.add(question)
        session.add(group)


def _attach_ungrouped_questions_from_payload(
    version: BaselineTemplateDefinition,
    questions: List[QuestionCreate],
    session: AsyncSession,
) -> None:
    """Build and attach ungrouped BaselineQuestion rows directly on the version."""
    for q in questions:
        question = BaselineQuestion(
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
        )
        for opt in q.options:
            BaselineQuestionOption(
                question=question,
                value=opt.value,
                display_order=opt.display_order,
            )
        session.add(question)


async def list_baseline_templates(
    session: AsyncSession,
    category: Optional[str] = None,
    template_type: Optional[TemplateType] = None,
    level: Optional[int] = None,
    active_only: bool = True,
) -> List[BaselineTemplate]:
    """List all baseline templates."""
    query = select(BaselineTemplate)

    if active_only:
        query = query.where(BaselineTemplate.is_active == True)
    if category:
        query = query.where(BaselineTemplate.category == category)
    if template_type is not None:
        query = query.where(BaselineTemplate.template_type == template_type)
    if level is not None:
        query = query.where(BaselineTemplate.level == level)

    query = query.order_by(BaselineTemplate.template_type, BaselineTemplate.level, BaselineTemplate.name)
    result = await session.exec(query)
    return list(result.all())


async def get_baseline_template(
    template_id: UUID,
    session: AsyncSession,
    include_versions: bool = False,
) -> BaselineTemplate:
    """Get a baseline template by ID."""
    query = select(BaselineTemplate).where(BaselineTemplate.id == template_id)

    if include_versions:
        query = query.options(selectinload(BaselineTemplate.versions))
        query = query.options(
            selectinload(BaselineTemplate.versions)
            .selectinload(BaselineTemplateDefinition.question_groups)
            .selectinload(BaselineQuestionGroup.questions)
            .selectinload(BaselineQuestion.options),
            selectinload(BaselineTemplate.versions)
            .selectinload(BaselineTemplateDefinition.ungrouped_questions)
            .selectinload(BaselineQuestion.options),
        )

    result = await session.exec(query)
    template = result.first()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Baseline template not found.",
        )
    return template


async def get_baseline_template_by_name(
    name: str,
    session: AsyncSession,
) -> Optional[BaselineTemplate]:
    result = await session.exec(
        select(BaselineTemplate).where(BaselineTemplate.name == name)
    )
    return result.first()


async def get_baseline_template_by_type_level(
    template_type: TemplateType,
    level: int,
    session: AsyncSession,
) -> Optional[BaselineTemplate]:
    result = await session.exec(
        select(BaselineTemplate).where(
            BaselineTemplate.template_type == template_type,
            BaselineTemplate.level == level,
        )
    )
    return result.first()


async def update_baseline_template(
    template_id: UUID,
    data: BaselineTemplateUpdate,
    session: AsyncSession,
) -> BaselineTemplate:
    """Update a baseline template header (admin-only)."""
    template = await get_baseline_template(template_id, session)

    if template.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update a locked baseline template.",
        )

    updates = data.model_dump(exclude_unset=True)

    if "active_version_id" in updates and updates["active_version_id"]:
        version = await get_baseline_template_definition(
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


async def delete_baseline_template(
    template_id: UUID,
    session: AsyncSession,
    force: bool = False,
) -> None:
    """Delete a baseline template (admin-only, non-locked only)."""
    template = await get_baseline_template(template_id, session)

    if template.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete a locked baseline template.",
        )

    # `force` is reserved for future "tenant extension" safeguards.
    _ = force
    await session.delete(template)
    await session.commit()


# ── Definition Management ─────────────────────────────────────────────

async def create_baseline_template_definition(
    template_id: UUID,
    data: BaselineTemplateDefinitionCreate,
    session: AsyncSession,
    copy_from_version_id: Optional[UUID] = None,
) -> BaselineTemplateDefinition:
    """Create a new version for a baseline template (admin-only).

    If copy_from_version_id is provided, all question groups/questions/options
    from that version are copied forward into the new draft.
    """
    template = await get_baseline_template(template_id, session)

    if template.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot add versions to a locked baseline template.",
        )

    version = BaselineTemplateDefinition(
        template_id=template.id,
        version_tag=data.version_tag,
        rules_config=data.rules_config,
        changelog=data.changelog,
        is_draft=True,
    )
    session.add(version)
    await session.flush()

    if copy_from_version_id:
        source = await _load_definition_with_groups(copy_from_version_id, session)
        _copy_groups_from_definition(source, version)
    else:
        if data.question_groups:
            _attach_question_groups_from_payload(version, data.question_groups, session)
        if data.questions:
            _attach_ungrouped_questions_from_payload(version, data.questions, session)

    await session.commit()
    return await _load_definition_with_groups(version.id, session)


async def get_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
) -> BaselineTemplateDefinition:
    """Get a specific definition/version of a baseline template (with nested rows)."""
    version = await _load_definition_with_groups(version_id, session)
    if version.template_id != template_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Baseline template definition not found.",
        )
    return version


async def update_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    data: BaselineTemplateDefinitionUpdate,
    session: AsyncSession,
) -> BaselineTemplateDefinition:
    """Update a baseline template definition's metadata (draft only, admin-only)."""
    version = await get_baseline_template_definition(template_id, version_id, session)

    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a published definition. Create a new version instead.",
        )

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(version, key, value)

    session.add(version)
    await session.commit()
    await session.refresh(version)
    return version


async def delete_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a draft baseline template definition (admin-only)."""
    version = await get_baseline_template_definition(template_id, version_id, session)

    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a published definition.",
        )

    template = await get_baseline_template(template_id, session)
    if template.active_version_id == version.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the active version. Set a different active version first.",
        )

    await session.delete(version)
    await session.commit()


async def publish_baseline_template_definition(
    template_id: UUID,
    version_id: UUID,
    session: AsyncSession,
    set_as_active: bool = True,
) -> BaselineTemplateDefinition:
    """Publish a draft baseline template definition.

    Once published, the definition is immutable.
    """
    version = await get_baseline_template_definition(template_id, version_id, session)

    if not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Definition is already published.",
        )

    version.is_draft = False
    session.add(version)

    if set_as_active:
        template = await get_baseline_template(template_id, session)
        template.active_version_id = version.id
        session.add(template)

    await session.commit()
    await session.refresh(version)
    return version


# ── Question Group CRUD ───────────────────────────────────────────────

async def add_question_group(
    template_id: UUID,
    version_id: UUID,
    data: QuestionGroupCreate,
    session: AsyncSession,
) -> BaselineQuestionGroup:
    """Add a question group to a draft baseline version."""
    version = await get_baseline_template_definition(template_id, version_id, session)
    _assert_draft(version)

    group = BaselineQuestionGroup(
        version_id=version.id,
        unique_key=data.unique_key,
        title=data.title,
        display_order=data.display_order,
        submit_api_url=data.submit_api_url,
        sequential_file_upload=data.sequential_file_upload,
    )
    for q in data.questions:
        _build_baseline_question(group, q, session)

    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def delete_question_group(
    group_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a question group from a draft baseline version."""
    group = await session.get(BaselineQuestionGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")

    version = await session.get(BaselineTemplateDefinition, group.version_id)
    _assert_draft(version)

    await session.delete(group)
    await session.commit()


# ── Question CRUD ─────────────────────────────────────────────────────

async def add_question(
    group_id: UUID,
    data: QuestionCreate,
    session: AsyncSession,
) -> BaselineQuestion:
    """Add a question to a draft baseline question group."""
    group = await session.get(BaselineQuestionGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")

    version = await session.get(BaselineTemplateDefinition, group.version_id)
    _assert_draft(version)

    question = _build_baseline_question(group, data, session)
    await session.commit()
    await session.refresh(question)
    return question


async def delete_question(
    question_id: UUID,
    session: AsyncSession,
) -> None:
    """Delete a question from a draft baseline group."""
    question = await session.get(BaselineQuestion, question_id)
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found.")

    group = await session.get(BaselineQuestionGroup, question.group_id)
    version = await session.get(BaselineTemplateDefinition, group.version_id)
    _assert_draft(version)

    await session.delete(question)
    await session.commit()


# ── Internal helpers ──────────────────────────────────────────────────

def _assert_draft(version: BaselineTemplateDefinition) -> None:
    if not version or not version.is_draft:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a published baseline template definition.",
        )


def _build_baseline_question(
    group: BaselineQuestionGroup,
    data: QuestionCreate,
    session: AsyncSession,
) -> BaselineQuestion:
    question = BaselineQuestion(
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
    )
    for opt in data.options:
        BaselineQuestionOption(
            question=question,
            value=opt.value,
            display_order=opt.display_order,
        )
    session.add(question)
    return question
