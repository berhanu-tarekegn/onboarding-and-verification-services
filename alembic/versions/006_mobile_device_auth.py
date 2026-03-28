"""006 — public tables for mobile device registration and challenge login.

Revision ID: 006_mobile_device_auth
Revises: 005_tenant_key_and_verification_workflows
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa


revision = "006_mobile_device_auth"
down_revision = "005_tenant_key_and_verification_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_key", sa.String(length=63), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("login_hint", sa.String(length=255), nullable=True),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="ed25519"),
        sa.Column("public_key_b64u", sa.String(length=512), nullable=False),
        sa.Column("roles_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("client_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("pin_protected", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], name="fk_device_credentials_tenant_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_key", "subject", "device_id", name="uq_device_credentials_subject_device"),
        schema="public",
    )
    op.create_index("ix_device_credentials_tenant_id", "device_credentials", ["tenant_id"], schema="public")
    op.create_index("ix_device_credentials_tenant_key", "device_credentials", ["tenant_key"], schema="public")
    op.create_index("ix_device_credentials_subject", "device_credentials", ["subject"], schema="public")
    op.create_index("ix_device_credentials_device_id", "device_credentials", ["device_id"], schema="public")
    op.create_index("ix_device_credentials_is_active", "device_credentials", ["is_active"], schema="public")

    op.create_table(
        "device_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_key", sa.String(length=63), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=False),
        sa.Column("signing_input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.ForeignKeyConstraint(["credential_id"], ["public.device_credentials.id"], name="fk_device_challenges_credential_id"),
        sa.PrimaryKeyConstraint("id"),
        schema="public",
    )
    op.create_index("ix_device_challenges_credential_id", "device_challenges", ["credential_id"], schema="public")
    op.create_index("ix_device_challenges_tenant_key", "device_challenges", ["tenant_key"], schema="public")
    op.create_index("ix_device_challenges_subject", "device_challenges", ["subject"], schema="public")
    op.create_index("ix_device_challenges_device_id", "device_challenges", ["device_id"], schema="public")
    op.create_index("ix_device_challenges_status", "device_challenges", ["status"], schema="public")


def downgrade() -> None:
    op.drop_index("ix_device_challenges_status", table_name="device_challenges", schema="public")
    op.drop_index("ix_device_challenges_device_id", table_name="device_challenges", schema="public")
    op.drop_index("ix_device_challenges_subject", table_name="device_challenges", schema="public")
    op.drop_index("ix_device_challenges_tenant_key", table_name="device_challenges", schema="public")
    op.drop_index("ix_device_challenges_credential_id", table_name="device_challenges", schema="public")
    op.drop_table("device_challenges", schema="public")

    op.drop_index("ix_device_credentials_is_active", table_name="device_credentials", schema="public")
    op.drop_index("ix_device_credentials_device_id", table_name="device_credentials", schema="public")
    op.drop_index("ix_device_credentials_subject", table_name="device_credentials", schema="public")
    op.drop_index("ix_device_credentials_tenant_key", table_name="device_credentials", schema="public")
    op.drop_index("ix_device_credentials_tenant_id", table_name="device_credentials", schema="public")
    op.drop_table("device_credentials", schema="public")
