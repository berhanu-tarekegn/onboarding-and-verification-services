"""005 — Rename tenant schema_name to tenant_key and add verification workflow columns.

Revision ID: 005
Revises: 004
Create Date: 2026-03-27
"""

from alembic import context, op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _is_tenant_context() -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    schema = (x_args.get("tenant_schema") or "").strip().lower()
    return bool(schema) and schema != "public"


def _rename_public_tenant_key() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("tenants", schema="public")}
    if "schema_name" not in columns or "tenant_key" in columns:
        return

    op.alter_column("tenants", "schema_name", new_column_name="tenant_key", schema="public")
    op.execute("ALTER INDEX IF EXISTS public.ix_tenants_schema_name RENAME TO ix_tenants_tenant_key")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_tenants_schema_name'
                  AND conrelid = 'public.tenants'::regclass
            ) THEN
                ALTER TABLE public.tenants RENAME CONSTRAINT uq_tenants_schema_name TO uq_tenants_tenant_key;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_tenants_schema_name_valid'
                  AND conrelid = 'public.tenants'::regclass
            ) THEN
                ALTER TABLE public.tenants RENAME CONSTRAINT ck_tenants_schema_name_valid TO ck_tenants_tenant_key_valid;
            END IF;
        END
        $$;
        """
    )


def _rename_public_schema_name() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("tenants", schema="public")}
    if "tenant_key" not in columns or "schema_name" in columns:
        return

    op.alter_column("tenants", "tenant_key", new_column_name="schema_name", schema="public")
    op.execute("ALTER INDEX IF EXISTS public.ix_tenants_tenant_key RENAME TO ix_tenants_schema_name")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_tenants_tenant_key'
                  AND conrelid = 'public.tenants'::regclass
            ) THEN
                ALTER TABLE public.tenants RENAME CONSTRAINT uq_tenants_tenant_key TO uq_tenants_schema_name;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_tenants_tenant_key_valid'
                  AND conrelid = 'public.tenants'::regclass
            ) THEN
                ALTER TABLE public.tenants RENAME CONSTRAINT ck_tenants_tenant_key_valid TO ck_tenants_schema_name_valid;
            END IF;
        END
        $$;
        """
    )


def _add_verification_workflow_columns() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("verification_runs")}
    if "workflow_id" not in columns:
        op.add_column("verification_runs", sa.Column("workflow_id", sa.String(length=255), nullable=True))
    if "workflow_run_id" not in columns:
        op.add_column("verification_runs", sa.Column("workflow_run_id", sa.String(length=255), nullable=True))
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("verification_runs")}
    if "ix_verification_runs_workflow_id" not in existing_indexes:
        op.create_index("ix_verification_runs_workflow_id", "verification_runs", ["workflow_id"])


def _drop_verification_workflow_columns() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("verification_runs")}
    if "ix_verification_runs_workflow_id" in existing_indexes:
        op.drop_index("ix_verification_runs_workflow_id", table_name="verification_runs")
    columns = {col["name"] for col in inspector.get_columns("verification_runs")}
    if "workflow_run_id" in columns:
        op.drop_column("verification_runs", "workflow_run_id")
    if "workflow_id" in columns:
        op.drop_column("verification_runs", "workflow_id")


def upgrade() -> None:
    if _is_tenant_context():
        _add_verification_workflow_columns()
        return

    _rename_public_tenant_key()


def downgrade() -> None:
    if _is_tenant_context():
        _drop_verification_workflow_columns()
        return

    _rename_public_schema_name()
