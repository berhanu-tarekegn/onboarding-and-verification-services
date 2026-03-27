"""004 — Tenant verification runtime tables.

Adds per-submission verification execution state so configurable onboarding
checks can be started, deferred, resumed, and evaluated independently of the
template definition itself.

Revision ID: 004
Revises: 003
Create Date: 2026-03-27
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def _is_tenant_context() -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    schema = (x_args.get("tenant_schema") or "").strip().lower()
    return bool(schema) and schema != "public"


def upgrade() -> None:
    if not _is_tenant_context():
        return

    op.create_table(
        "verification_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flow_key", sa.String(length=100), nullable=False, server_default="default"),
        sa.Column("journey", sa.String(length=50), nullable=False, server_default="self_service_online"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(length=50), nullable=True),
        sa.Column("kyc_level", sa.String(length=100), nullable=True),
        sa.Column("current_step_key", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("rules_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("facts_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_verification_run_submission"),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["tenant_template_definitions.id"],
            ondelete="RESTRICT",
            name="fk_verification_run_template_version",
        ),
    )
    op.create_index("ix_verification_runs_submission_id", "verification_runs", ["submission_id"])
    op.create_index("ix_verification_runs_template_version_id", "verification_runs", ["template_version_id"])
    op.create_index("ix_verification_runs_status", "verification_runs", ["status"])
    op.create_index("ix_verification_runs_flow_key", "verification_runs", ["flow_key"])
    op.create_index("ix_verification_runs_is_active", "verification_runs", ["is_active"])

    op.create_table(
        "verification_step_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("step_type", sa.String(length=50), nullable=False),
        sa.Column("adapter_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("outcome", sa.String(length=50), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("waiting_for", sa.String(length=100), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("depends_on", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("result_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("action_schema", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error_details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["run_id"], ["verification_runs.id"], ondelete="CASCADE", name="fk_verification_step_run_run"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE", name="fk_verification_step_run_submission"),
        sa.UniqueConstraint("run_id", "step_key", name="uq_verification_step_run_key"),
    )
    op.create_index("ix_verification_step_runs_run_id", "verification_step_runs", ["run_id"])
    op.create_index("ix_verification_step_runs_submission_id", "verification_step_runs", ["submission_id"])
    op.create_index("ix_verification_step_runs_step_key", "verification_step_runs", ["step_key"])
    op.create_index("ix_verification_step_runs_status", "verification_step_runs", ["status"])
    op.create_index("ix_verification_step_runs_correlation_id", "verification_step_runs", ["correlation_id"])


def downgrade() -> None:
    if not _is_tenant_context():
        return

    op.drop_index("ix_verification_step_runs_correlation_id", table_name="verification_step_runs")
    op.drop_index("ix_verification_step_runs_status", table_name="verification_step_runs")
    op.drop_index("ix_verification_step_runs_step_key", table_name="verification_step_runs")
    op.drop_index("ix_verification_step_runs_submission_id", table_name="verification_step_runs")
    op.drop_index("ix_verification_step_runs_run_id", table_name="verification_step_runs")
    op.drop_table("verification_step_runs")

    op.drop_index("ix_verification_runs_is_active", table_name="verification_runs")
    op.drop_index("ix_verification_runs_flow_key", table_name="verification_runs")
    op.drop_index("ix_verification_runs_status", table_name="verification_runs")
    op.drop_index("ix_verification_runs_template_version_id", table_name="verification_runs")
    op.drop_index("ix_verification_runs_submission_id", table_name="verification_runs")
    op.drop_table("verification_runs")
