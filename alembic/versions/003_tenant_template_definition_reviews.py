"""003 — Tenant template definition review workflow.

Adds review status fields and immutable review history for tenant template
definitions.

Revision ID: 003
Revises: 002
Create Date: 2026-03-26
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
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

    if not _type_exists("definitionreviewstatus", schema):
        op.execute(
            "CREATE TYPE definitionreviewstatus AS ENUM "
            "('draft','pending_review','approved','changes_requested')"
        )
    if not _type_exists("definitionreviewaction", schema):
        op.execute(
            "CREATE TYPE definitionreviewaction AS ENUM "
            "('submitted','approved','changes_requested')"
        )

    op.add_column(
        "tenant_template_definitions",
        sa.Column("review_status", sa.Text(), nullable=False, server_default="draft"),
    )
    op.execute(
        "ALTER TABLE tenant_template_definitions "
        "ALTER COLUMN review_status DROP DEFAULT, "
        "ALTER COLUMN review_status TYPE definitionreviewstatus "
        "USING review_status::definitionreviewstatus, "
        "ALTER COLUMN review_status SET DEFAULT 'draft'::definitionreviewstatus"
    )
    op.add_column(
        "tenant_template_definitions",
        sa.Column("submitted_for_review_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_template_definitions",
        sa.Column("submitted_for_review_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "tenant_template_definitions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_template_definitions",
        sa.Column("reviewed_by", sa.String(255), nullable=True),
    )
    op.add_column(
        "tenant_template_definitions",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "tenant_template_definition_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["tenant_template_definitions.id"],
            ondelete="CASCADE",
            name="fk_tenant_template_definition_review_definition",
        ),
    )
    op.execute(
        "ALTER TABLE tenant_template_definition_reviews "
        "ALTER COLUMN action TYPE definitionreviewaction "
        "USING action::definitionreviewaction"
    )
    op.create_index(
        "ix_tenant_template_definition_reviews_definition_id",
        "tenant_template_definition_reviews",
        ["definition_id"],
    )


def downgrade() -> None:
    if not _is_tenant_context():
        return

    op.drop_index(
        "ix_tenant_template_definition_reviews_definition_id",
        table_name="tenant_template_definition_reviews",
    )
    op.drop_table("tenant_template_definition_reviews")
    op.drop_column("tenant_template_definitions", "review_notes")
    op.drop_column("tenant_template_definitions", "reviewed_by")
    op.drop_column("tenant_template_definitions", "reviewed_at")
    op.drop_column("tenant_template_definitions", "submitted_for_review_by")
    op.drop_column("tenant_template_definitions", "submitted_for_review_at")
    op.drop_column("tenant_template_definitions", "review_status")
    sa.Enum(name="definitionreviewaction").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="definitionreviewstatus").drop(op.get_bind(), checkfirst=True)
