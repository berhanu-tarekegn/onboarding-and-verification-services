"""001 — Fresh public schema baseline.

Creates all shared public-schema objects for a fresh installation.

Revision ID: 001
Revises: 9b7c008c68a7
Create Date: 2026-03-26
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = "9b7c008c68a7"
branch_labels = None
depends_on = None


def _is_tenant_context() -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    schema = (x_args.get("tenant_schema") or "").strip().lower()
    return bool(schema) and schema != "public"


def upgrade() -> None:
    if _is_tenant_context():
        return

    bind = op.get_bind()
    bind.execute(sa.text("CREATE TYPE public.templatetype AS ENUM ('kyc', 'kyb')"))

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("keycloak_realm", sa.String(64), nullable=True),
        sa.Column("keycloak_client_id", sa.String(255), nullable=True),
        sa.Column("keycloak_client_secret", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.CheckConstraint("schema_name ~ '^[a-z][a-z0-9_]*$'", name="ck_tenants_schema_name_valid"),
        sa.UniqueConstraint("schema_name", name="uq_tenants_schema_name"),
        sa.UniqueConstraint("keycloak_realm", name="uq_tenants_keycloak_realm"),
        schema="public",
    )
    op.create_index("ix_tenants_name", "tenants", ["name"], schema="public")
    op.create_index("ix_tenants_schema_name", "tenants", ["schema_name"], unique=True, schema="public")
    op.create_index("ix_tenants_keycloak_realm", "tenants", ["keycloak_realm"], unique=True, schema="public")

    op.create_table(
        "baseline_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "template_type",
            postgresql.ENUM(
                "kyc",
                "kyb",
                name="templatetype",
                schema="public",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(100), nullable=False, server_default="general"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.CheckConstraint("level >= 1", name="ck_baseline_templates_level_positive"),
        sa.UniqueConstraint("template_type", "level", name="uq_baseline_template_type_level"),
        sa.UniqueConstraint("name", name="uq_baseline_templates_name"),
        schema="public",
    )
    op.create_index("ix_baseline_templates_name", "baseline_templates", ["name"], schema="public")
    op.create_index("ix_baseline_templates_template_type", "baseline_templates", ["template_type"], schema="public")
    op.create_index("ix_baseline_templates_level", "baseline_templates", ["level"], schema="public")
    op.create_index("ix_baseline_templates_category", "baseline_templates", ["category"], schema="public")

    op.create_table(
        "baseline_template_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_tag", sa.String(50), nullable=False),
        sa.Column(
            "rules_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["template_id"], ["public.baseline_templates.id"], ondelete="CASCADE", name="fk_baseline_def_template"),
        schema="public",
    )
    op.create_index("ix_baseline_template_definitions_template_id", "baseline_template_definitions", ["template_id"], schema="public")
    op.create_index("ix_baseline_template_definitions_version_tag", "baseline_template_definitions", ["version_tag"], schema="public")
    op.create_foreign_key(
        "fk_baseline_template_active_version",
        "baseline_templates",
        "baseline_template_definitions",
        ["active_version_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="SET NULL",
    )

    op.create_table(
        "baseline_question_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unique_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submit_api_url", sa.String(500), nullable=True),
        sa.Column("sequential_file_upload", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["version_id"], ["public.baseline_template_definitions.id"], ondelete="CASCADE", name="fk_baseline_qgroup_version"),
        sa.UniqueConstraint("version_id", "unique_key", name="uq_baseline_qgroup_version_key"),
        schema="public",
    )
    op.create_index("ix_baseline_question_groups_version_id", "baseline_question_groups", ["version_id"], schema="public")

    op.create_table(
        "baseline_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unique_key", sa.String(255), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("field_type", sa.String(50), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regex", sa.Text(), nullable=True),
        sa.Column("keyboard_type", sa.String(50), nullable=True),
        sa.Column("min_date", sa.String(10), nullable=True),
        sa.Column("max_date", sa.String(10), nullable=True),
        sa.Column("depends_on_unique_key", sa.String(255), nullable=True),
        sa.Column("visible_when_equals", sa.String(255), nullable=True),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["group_id"], ["public.baseline_question_groups.id"], ondelete="CASCADE", name="fk_baseline_question_group"),
        sa.ForeignKeyConstraint(["version_id"], ["public.baseline_template_definitions.id"], ondelete="CASCADE", name="fk_baseline_question_version"),
        sa.CheckConstraint("field_type IN ('text','dropdown','radio','checkbox','date','fileUpload','signature')", name="ck_baseline_question_field_type"),
        sa.CheckConstraint("(group_id IS NOT NULL OR version_id IS NOT NULL)", name="ck_baseline_question_has_parent"),
        sa.UniqueConstraint("group_id", "unique_key", name="uq_baseline_question_group_key"),
        schema="public",
    )
    op.create_index("ix_baseline_questions_group_id", "baseline_questions", ["group_id"], schema="public")
    op.create_index("ix_baseline_questions_version_id", "baseline_questions", ["version_id"], schema="public")

    op.create_table(
        "baseline_question_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["question_id"], ["public.baseline_questions.id"], ondelete="CASCADE", name="fk_baseline_qoption_question"),
        schema="public",
    )
    op.create_index("ix_baseline_question_options_question_id", "baseline_question_options", ["question_id"], schema="public")

    op.create_table(
        "authz_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        schema="public",
    )
    op.create_index("ix_authz_policies_scope", "authz_policies", ["scope"], schema="public")
    op.create_index("ix_authz_policies_tenant_id", "authz_policies", ["tenant_id"], schema="public")
    bind.execute(
        sa.text(
            """
            INSERT INTO public.authz_policies
                (id, scope, tenant_id, version, policy, created_by, updated_by)
            VALUES
                (gen_random_uuid(), 'global', NULL, 1, '{}'::jsonb, 'system', 'system')
            """
        )
    )

    op.create_table(
        "identity_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("realm", sa.String(64), nullable=False),
        sa.Column("keycloak_user_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("national_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        schema="public",
    )
    op.create_index("ix_identity_links_tenant_id", "identity_links", ["tenant_id"], schema="public")
    op.create_index("ix_identity_links_realm", "identity_links", ["realm"], schema="public")
    op.create_index("ix_identity_links_keycloak_user_id", "identity_links", ["keycloak_user_id"], schema="public")
    op.create_index("ix_identity_links_national_id", "identity_links", ["national_id"], schema="public")


def downgrade() -> None:
    if _is_tenant_context():
        return

    op.drop_table("identity_links", schema="public")
    op.drop_table("authz_policies", schema="public")
    op.drop_table("baseline_question_options", schema="public")
    op.drop_table("baseline_questions", schema="public")
    op.drop_table("baseline_question_groups", schema="public")
    op.drop_constraint("fk_baseline_template_active_version", "baseline_templates", schema="public", type_="foreignkey")
    op.drop_table("baseline_template_definitions", schema="public")
    op.drop_table("baseline_templates", schema="public")
    op.drop_table("tenants", schema="public")
    sa.Enum(name="templatetype", schema="public").drop(op.get_bind(), checkfirst=True)
