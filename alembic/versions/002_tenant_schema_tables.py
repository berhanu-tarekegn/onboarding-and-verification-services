"""002 — Tenant schema baseline.

Creates all tenant-scoped tables and enums in a provisioned tenant schema.

Revision ID: 002
Revises: 001
Create Date: 2026-03-26
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def _is_tenant_context() -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    schema = (x_args.get("tenant_schema") or "").strip().lower()
    return bool(schema) and schema != "public"


def _current_schema() -> str:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT current_schema()")).scalar() or "public"


def _type_exists(type_name: str, schema: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_type t
            JOIN pg_namespace n ON t.typnamespace = n.oid
            WHERE t.typname = :t AND n.nspname = :s
            """
        ),
        {"t": type_name, "s": schema},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    if not _is_tenant_context():
        return

    schema = _current_schema()

    if not _type_exists("submissionstatus", schema):
        op.execute(
            "CREATE TYPE submissionstatus AS ENUM "
            "('draft','submitted','under_review','approved','rejected','returned','completed','cancelled')"
        )
    if not _type_exists("productstatus", schema):
        op.execute("CREATE TYPE productstatus AS ENUM ('draft','active','inactive')")
    if not _type_exists("rulesetstatus", schema):
        op.execute("CREATE TYPE rulesetstatus AS ENUM ('draft','published','archived')")
    if not _type_exists("transformoperation", schema):
        op.execute(
            "CREATE TYPE transformoperation AS ENUM ("
            "'identity','rename','map_values','coerce_type',"
            "'split','merge','default_value','compute','drop')"
        )

    op.create_table(
        "tenant_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template_type", sa.Text(), nullable=False),
        sa.Column("baseline_level", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.CheckConstraint("baseline_level >= 1", name="ck_tenant_templates_baseline_level_positive"),
    )
    op.create_index("ix_tenant_templates_name", "tenant_templates", ["name"])
    op.create_index("ix_tenant_templates_template_type", "tenant_templates", ["template_type"])
    op.create_index("ix_tenant_templates_baseline_level", "tenant_templates", ["baseline_level"])
    op.execute(
        "ALTER TABLE tenant_templates "
        "ALTER COLUMN template_type TYPE public.templatetype "
        "USING template_type::public.templatetype"
    )

    op.create_table(
        "tenant_template_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_tag", sa.String(50), nullable=False),
        sa.Column("copied_from_baseline_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rules_config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["template_id"], ["tenant_templates.id"], ondelete="CASCADE", name="fk_tenant_def_template"),
        sa.ForeignKeyConstraint(["copied_from_baseline_version_id"], ["public.baseline_template_definitions.id"], ondelete="SET NULL", name="fk_tenant_def_baseline_version"),
    )
    op.create_index("ix_tenant_template_definitions_template_id", "tenant_template_definitions", ["template_id"])
    op.create_index("ix_tenant_template_definitions_version_tag", "tenant_template_definitions", ["version_tag"])
    op.create_index("ix_tenant_template_definitions_baseline_version_id", "tenant_template_definitions", ["copied_from_baseline_version_id"])
    op.create_foreign_key(
        "fk_tenant_template_active_version",
        "tenant_templates",
        "tenant_template_definitions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "question_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unique_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submit_api_url", sa.String(500), nullable=True),
        sa.Column("sequential_file_upload", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_tenant_editable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["version_id"], ["tenant_template_definitions.id"], ondelete="CASCADE", name="fk_qgroup_version"),
        sa.UniqueConstraint("version_id", "unique_key", name="uq_qgroup_version_key"),
    )
    op.create_index("ix_question_groups_version_id", "question_groups", ["version_id"])

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unique_key", sa.String(255), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("field_type", sa.String(50), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regex", sa.Text(), nullable=True),
        sa.Column("keyboard_type", sa.String(50), nullable=True),
        sa.Column("min_date", sa.String(10), nullable=True),
        sa.Column("max_date", sa.String(10), nullable=True),
        sa.Column("depends_on_unique_key", sa.String(255), nullable=True),
        sa.Column("visible_when_equals", sa.String(255), nullable=True),
        sa.Column("rules", postgresql.JSONB(), nullable=True),
        sa.Column("is_tenant_editable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["group_id"], ["question_groups.id"], ondelete="CASCADE", name="fk_question_group"),
        sa.ForeignKeyConstraint(["version_id"], ["tenant_template_definitions.id"], ondelete="CASCADE", name="fk_question_version"),
        sa.CheckConstraint("field_type IN ('text','dropdown','radio','checkbox','date','fileUpload','signature')", name="ck_question_field_type"),
        sa.CheckConstraint("(group_id IS NOT NULL OR version_id IS NOT NULL)", name="ck_question_has_parent"),
        sa.UniqueConstraint("group_id", "unique_key", name="uq_question_group_key"),
    )
    op.create_index("ix_questions_group_id", "questions", ["group_id"])
    op.create_index("ix_questions_version_id", "questions", ["version_id"])

    op.create_table(
        "question_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_tenant_editable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE", name="fk_qoption_question"),
    )
    op.create_index("ix_question_options_question_id", "question_options", ["question_id"])

    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("form_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("computed_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("validation_results", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("submitter_id", sa.String(255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("external_ref", sa.String(255), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["template_id"], ["tenant_templates.id"], ondelete="RESTRICT", name="fk_submission_template"),
        sa.ForeignKeyConstraint(["template_version_id"], ["tenant_template_definitions.id"], ondelete="RESTRICT", name="fk_submission_template_version"),
        sa.ForeignKeyConstraint(["baseline_version_id"], ["public.baseline_template_definitions.id"], ondelete="SET NULL", name="fk_submission_baseline_version"),
    )
    op.execute(
        "ALTER TABLE submissions "
        "ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE submissionstatus USING status::submissionstatus, "
        "ALTER COLUMN status SET DEFAULT 'draft'::submissionstatus"
    )
    op.create_index("ix_submissions_template_id", "submissions", ["template_id"])
    op.create_index("ix_submissions_template_version_id", "submissions", ["template_version_id"])
    op.create_index("ix_submissions_status", "submissions", ["status"])
    op.create_index("ix_submissions_submitter_id", "submissions", ["submitter_id"])
    op.create_index("ix_submissions_external_ref", "submissions", ["external_ref"])

    op.create_table(
        "submission_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("extra_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_status_history_submission"),
    )
    op.execute(
        "ALTER TABLE submission_status_history "
        "ALTER COLUMN from_status TYPE submissionstatus USING from_status::submissionstatus, "
        "ALTER COLUMN to_status TYPE submissionstatus USING to_status::submissionstatus"
    )
    op.create_index("ix_submission_status_history_submission_id", "submission_status_history", ["submission_id"])

    op.create_table(
        "submission_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("field_id", sa.String(255), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_comment_submission"),
        sa.ForeignKeyConstraint(["parent_id"], ["submission_comments.id"], ondelete="SET NULL", name="fk_comment_parent"),
    )

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("product_code", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["template_id"], ["tenant_templates.id"], ondelete="SET NULL", name="fk_product_template"),
    )
    op.execute(
        "ALTER TABLE products "
        "ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE productstatus USING status::productstatus, "
        "ALTER COLUMN status SET DEFAULT 'draft'::productstatus"
    )
    op.create_index("ix_products_name", "products", ["name"])
    op.create_index("ix_products_product_code", "products", ["product_code"], unique=True)
    op.create_index("ix_products_status", "products", ["status"])

    op.create_foreign_key(
        "fk_submission_product",
        "submissions",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_submissions_product_id", "submissions", ["product_id"])

    op.create_table(
        "submission_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_type", sa.String(50), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_answer_submission"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="RESTRICT", name="fk_answer_question"),
        sa.UniqueConstraint("submission_id", "question_id", name="uq_answer_submission_question"),
        sa.CheckConstraint("field_type IN ('text','dropdown','radio','checkbox','date','fileUpload','signature')", name="ck_answer_field_type_valid"),
        sa.CheckConstraint("field_type <> 'date' OR answer IS NULL OR answer ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'", name="ck_answer_date_format"),
        sa.CheckConstraint("field_type <> 'checkbox' OR answer IS NULL OR length(answer) > 0", name="ck_answer_checkbox_nonempty"),
        sa.CheckConstraint("field_type <> 'fileUpload' OR answer IS NULL OR length(trim(answer)) > 0", name="ck_answer_fileupload_nonempty"),
    )
    op.create_index("ix_submission_answers_submission_id", "submission_answers", ["submission_id"])
    op.create_index("ix_submission_answers_question_id", "submission_answers", ["question_id"])

    op.create_table(
        "transform_rule_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("auto_generated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["template_id"], ["tenant_templates.id"], ondelete="CASCADE", name="fk_ruleset_template"),
        sa.ForeignKeyConstraint(["source_version_id"], ["tenant_template_definitions.id"], ondelete="CASCADE", name="fk_ruleset_source_version"),
        sa.ForeignKeyConstraint(["target_version_id"], ["tenant_template_definitions.id"], ondelete="CASCADE", name="fk_ruleset_target_version"),
        sa.UniqueConstraint("source_version_id", "target_version_id", name="uq_ruleset_version_pair"),
    )
    op.execute(
        "ALTER TABLE transform_rule_sets "
        "ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE rulesetstatus USING status::rulesetstatus, "
        "ALTER COLUMN status SET DEFAULT 'draft'::rulesetstatus"
    )
    op.create_index("ix_transform_rule_sets_template_id", "transform_rule_sets", ["template_id"])
    op.create_index("ix_transform_rule_sets_source_version_id", "transform_rule_sets", ["source_version_id"])
    op.create_index("ix_transform_rule_sets_target_version_id", "transform_rule_sets", ["target_version_id"])

    op.create_table(
        "transform_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("rule_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_unique_key", sa.String(255), nullable=True),
        sa.Column("target_unique_key", sa.String(255), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["rule_set_id"], ["transform_rule_sets.id"], ondelete="CASCADE", name="fk_rule_rule_set"),
    )
    op.execute(
        "ALTER TABLE transform_rules "
        "ALTER COLUMN operation TYPE transformoperation "
        "USING operation::transformoperation"
    )
    op.create_index("ix_transform_rules_rule_set_id", "transform_rules", ["rule_set_id"])

    op.create_table(
        "transform_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("after_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("applied_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("is_preview", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_tlog_submission"),
        sa.ForeignKeyConstraint(["rule_set_id"], ["transform_rule_sets.id"], ondelete="RESTRICT", name="fk_tlog_rule_set"),
        sa.ForeignKeyConstraint(["source_version_id"], ["tenant_template_definitions.id"], ondelete="RESTRICT", name="fk_tlog_source_version"),
        sa.ForeignKeyConstraint(["target_version_id"], ["tenant_template_definitions.id"], ondelete="RESTRICT", name="fk_tlog_target_version"),
    )
    op.create_index("ix_transform_logs_submission_id", "transform_logs", ["submission_id"])
    op.create_index("ix_transform_logs_rule_set_id", "transform_logs", ["rule_set_id"])


def downgrade() -> None:
    if not _is_tenant_context():
        return

    op.drop_table("transform_logs")
    op.drop_index("ix_transform_rules_rule_set_id", table_name="transform_rules")
    op.drop_table("transform_rules")
    op.drop_index("ix_transform_rule_sets_target_version_id", table_name="transform_rule_sets")
    op.drop_index("ix_transform_rule_sets_source_version_id", table_name="transform_rule_sets")
    op.drop_index("ix_transform_rule_sets_template_id", table_name="transform_rule_sets")
    op.drop_table("transform_rule_sets")
    sa.Enum(name="transformoperation").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="rulesetstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_table("submission_answers")
    op.drop_index("ix_submissions_product_id", table_name="submissions")
    op.drop_constraint("fk_submission_product", "submissions", type_="foreignkey")
    op.drop_table("products")
    sa.Enum(name="productstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_table("submission_comments")
    op.drop_table("submission_status_history")
    op.drop_table("submissions")
    sa.Enum(name="submissionstatus").drop(op.get_bind(), checkfirst=True)
    op.drop_table("question_options")
    op.drop_table("questions")
    op.drop_table("question_groups")
    op.drop_constraint("fk_tenant_template_active_version", "tenant_templates", type_="foreignkey")
    op.drop_table("tenant_template_definitions")
    op.drop_table("tenant_templates")
