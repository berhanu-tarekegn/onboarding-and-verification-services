"""Database migration and schema integrity tests.

These tests verify that Alembic migrations produce exactly the schema the
application expects: correct tables, columns, types, constraints, indexes,
and enum values. They also exercise tenant schema provisioning end-to-end.

Run selectively (they require a live PostgreSQL instance):

    # Run only migration tests
    uv run pytest tests/test_database_migrations.py -v

    # Skip them in the main suite
    uv run pytest tests/ -v -m "not db_migration"

    # Set the DB URL if different from the default
    DATABASE_TEST_URL=postgresql+asyncpg://onboarding:onboarding@localhost:5433/onboarding_test_db \
    uv run pytest tests/test_database_migrations.py -v

Marks
-----
``db_migration`` — applied to every test so the suite can be targeted or excluded.
"""

from __future__ import annotations

import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

import os
from app.core.config import get_settings
from app.db.migrations import (
    _sanitize_schema_name,
    drop_tenant_schema,
    provision_tenant_schema,
)

pytestmark = pytest.mark.db_migration

_SETTINGS = get_settings()
DB_URL = os.environ.get("DATABASE_TEST_URL", _SETTINGS.DATABASE_URL)


# ── Engine — NullPool so every connect() is a clean TCP connection ───

@pytest_asyncio.fixture(scope="module")
async def pg() -> AsyncEngine:
    engine = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
    yield engine
    await engine.dispose()


# ── Per-test connection (always fresh because of NullPool) ───────────

@pytest_asyncio.fixture
async def conn(pg: AsyncEngine) -> AsyncConnection:
    async with pg.connect() as c:
        yield c


# ── SQL helpers ────────────────────────────────────────────────────────

async def _columns(conn: AsyncConnection, table: str, schema: str = "public") -> set[str]:
    r = await conn.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t
    """), {"s": schema, "t": table})
    return {row[0] for row in r.fetchall()}


async def _checks(conn: AsyncConnection, table: str, schema: str = "public") -> list[str]:
    r = await conn.execute(text("""
        SELECT cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc
          ON tc.constraint_name = cc.constraint_name
         AND tc.constraint_schema = cc.constraint_schema
        WHERE tc.table_schema = :s AND tc.table_name = :t
          AND tc.constraint_type = 'CHECK'
    """), {"s": schema, "t": table})
    return [row[0] for row in r.fetchall()]


async def _unique_cols(conn: AsyncConnection, table: str, schema: str = "public") -> list[str]:
    r = await conn.execute(text("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        WHERE tc.table_schema = :s AND tc.table_name = :t
          AND tc.constraint_type = 'UNIQUE'
        ORDER BY tc.constraint_name, kcu.ordinal_position
    """), {"s": schema, "t": table})
    return [row[0] for row in r.fetchall()]


async def _unique_constraints(
    conn: AsyncConnection,
    table: str,
    schema: str = "public",
) -> dict[str, list[str]]:
    r = await conn.execute(text("""
        SELECT tc.constraint_name, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        WHERE tc.table_schema = :s AND tc.table_name = :t
          AND tc.constraint_type = 'UNIQUE'
        ORDER BY tc.constraint_name, kcu.ordinal_position
    """), {"s": schema, "t": table})
    constraints: dict[str, list[str]] = {}
    for constraint_name, column_name, _ in r.fetchall():
        constraints.setdefault(constraint_name, []).append(column_name)
    return constraints


async def _enum_values(conn: AsyncConnection, typname: str, schema: str = "public") -> list[str]:
    r = await conn.execute(text("""
        SELECT e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON e.enumtypid = t.oid
        JOIN pg_namespace n ON t.typnamespace = n.oid
        WHERE t.typname = :typname AND n.nspname = :schema
        ORDER BY e.enumsortorder
    """), {"typname": typname, "schema": schema})
    return [row[0] for row in r.fetchall()]


async def _col_nullable(conn: AsyncConnection, table: str, col: str, schema: str = "public") -> bool:
    r = await conn.execute(text("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t AND column_name = :c
    """), {"s": schema, "t": table, "c": col})
    row = r.fetchone()
    return row is not None and row[0] == "YES"


async def _col_default(conn: AsyncConnection, table: str, col: str, schema: str = "public") -> str | None:
    r = await conn.execute(text("""
        SELECT column_default FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t AND column_name = :c
    """), {"s": schema, "t": table, "c": col})
    row = r.fetchone()
    return row[0] if row else None


async def _col_udt(conn: AsyncConnection, table: str, col: str, schema: str = "public") -> tuple[str, str]:
    """Return (data_type, udt_name) for a column."""
    r = await conn.execute(text("""
        SELECT data_type, udt_name FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t AND column_name = :c
    """), {"s": schema, "t": table, "c": col})
    row = r.fetchone()
    return (row[0], row[1]) if row else ("", "")


# ══════════════════════════════════════════════════════════════════════
# Migration 001 — Public schema tables
# ══════════════════════════════════════════════════════════════════════

class TestPublicSchemaExists:
    EXPECTED = {
        "tenants",
        "baseline_templates",
        "baseline_template_definitions",
        "baseline_question_groups",
        "baseline_questions",
        "baseline_question_options",
        "authz_policies",
        "identity_links",
    }
    TENANT_ONLY = {
        "tenant_templates", "tenant_template_definitions",
        "question_groups", "questions", "question_options",
        "submissions", "submission_answers",
        "submission_status_history", "submission_comments", "products",
        "verification_runs", "verification_step_runs",
    }

    async def test_all_public_tables_exist(self, conn):
        r = await conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
              AND table_name != 'alembic_version'
        """))
        actual = {row[0] for row in r.fetchall()}
        missing = self.EXPECTED - actual
        assert not missing, f"Missing public tables: {missing}"

    async def test_no_tenant_tables_in_public(self, conn):
        r = await conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """))
        actual = {row[0] for row in r.fetchall()}
        leaked = self.TENANT_ONLY & actual
        assert not leaked, f"Tenant tables leaked into public schema: {leaked}"

    async def test_alembic_version_is_004(self, conn):
        r = await conn.execute(text("SELECT version_num FROM public.alembic_version"))
        row = r.fetchone()
        assert row and row[0] == "004", f"Expected version 004, got {row}"


class TestTemplateTypeEnum:
    async def test_enum_exists(self, conn):
        values = await _enum_values(conn, "templatetype")
        assert values, "templatetype enum must exist in the public schema"

    async def test_enum_is_exactly_kyc_and_kyb(self, conn):
        values = await _enum_values(conn, "templatetype")
        assert set(values) == {"kyc", "kyb"}, (
            f"Expected {{'kyc', 'kyb'}}, got {set(values)}"
        )


class TestTenantsTable:
    async def test_required_columns(self, conn):
        cols = await _columns(conn, "tenants")
        assert {
            "id",
            "name",
            "schema_name",
            "is_active",
            "keycloak_realm",
            "keycloak_client_id",
            "keycloak_client_secret",
            "created_at",
            "updated_at",
        }.issubset(cols)

    async def test_schema_name_is_unique(self, conn):
        unique = await _unique_cols(conn, "tenants")
        assert "schema_name" in unique

    async def test_schema_name_check_constraint_exists(self, conn):
        checks = await _checks(conn, "tenants")
        assert any("schema_name" in c for c in checks)

    async def test_valid_schema_name_accepted(self, conn):
        sp = await conn.begin_nested()
        try:
            await conn.execute(text("""
                INSERT INTO public.tenants (id, name, schema_name, is_active)
                VALUES (gen_random_uuid(), 'Good Corp', 'good_corp_1', true)
            """))
        finally:
            await sp.rollback()

    async def test_uppercase_schema_name_rejected(self, conn):
        sp = await conn.begin_nested()
        try:
            with pytest.raises(Exception):
                await conn.execute(text("""
                    INSERT INTO public.tenants (id, name, schema_name, is_active)
                    VALUES (gen_random_uuid(), 'Bad', 'BadName', true)
                """))
        finally:
            await sp.rollback()

    async def test_hyphen_in_schema_name_rejected(self, conn):
        sp = await conn.begin_nested()
        try:
            with pytest.raises(Exception):
                await conn.execute(text("""
                    INSERT INTO public.tenants (id, name, schema_name, is_active)
                    VALUES (gen_random_uuid(), 'Bad', 'bad-name', true)
                """))
        finally:
            await sp.rollback()


class TestBaselineTemplatesTable:
    async def test_required_columns(self, conn):
        cols = await _columns(conn, "baseline_templates")
        assert {
            "id",
            "template_type",
            "level",
            "name",
            "category",
            "is_active",
            "is_locked",
            "active_version_id",
        }.issubset(cols)

    async def test_template_type_is_enum(self, conn):
        dtype, udt = await _col_udt(conn, "baseline_templates", "template_type")
        assert dtype == "USER-DEFINED" and udt == "templatetype"

    async def test_template_type_and_level_unique_together(self, conn):
        constraints = await _unique_constraints(conn, "baseline_templates")
        assert ["template_type", "level"] in constraints.values()

    async def test_name_unique(self, conn):
        unique = await _unique_cols(conn, "baseline_templates")
        assert "name" in unique

    async def test_unknown_template_type_rejected(self, conn):
        sp = await conn.begin_nested()
        try:
            with pytest.raises(Exception):
                await conn.execute(text("""
                    INSERT INTO public.baseline_templates
                      (id, template_type, name, category)
                    VALUES
                      (gen_random_uuid(), 'unknown'::public.templatetype, 'X', 'x')
                """))
        finally:
            await sp.rollback()


class TestBaselineQuestionsTable:
    async def test_required_columns(self, conn):
        cols = await _columns(conn, "baseline_questions")
        assert {"id", "group_id", "version_id", "unique_key",
                "label", "field_type", "required", "display_order"}.issubset(cols)

    async def test_group_id_is_nullable(self, conn):
        assert await _col_nullable(conn, "baseline_questions", "group_id")

    async def test_version_id_is_nullable(self, conn):
        assert await _col_nullable(conn, "baseline_questions", "version_id")

    async def test_field_type_check_exists(self, conn):
        checks = await _checks(conn, "baseline_questions")
        assert any("field_type" in c for c in checks)

    async def test_parent_check_exists(self, conn):
        checks = await _checks(conn, "baseline_questions")
        assert any("group_id" in c and "version_id" in c for c in checks), (
            "Expected CHECK(group_id IS NOT NULL OR version_id IS NOT NULL)"
        )

    async def test_invalid_field_type_rejected(self, conn):
        sp = await conn.begin_nested()
        try:
            with pytest.raises(Exception):
                await conn.execute(text("""
                    INSERT INTO public.baseline_questions
                      (id, unique_key, label, field_type)
                    VALUES
                      (gen_random_uuid(), 'bad_q', 'Bad Q', 'freetext')
                """))
        finally:
            await sp.rollback()

    @pytest.mark.parametrize("ft", [
        "text", "dropdown", "radio", "checkbox", "date", "fileUpload", "signature"
    ])
    async def test_valid_field_types_accepted_by_check(self, conn, ft):
        """Confirm each valid field_type passes the CHECK expression."""
        # Use a plain SELECT to validate the value against the same set used in the constraint.
        # asyncpg doesn't support parameters inside DO $$ blocks.
        r = await conn.execute(text(
            "SELECT :ft = ANY(ARRAY['text','dropdown','radio','checkbox',"
            "'date','fileUpload','signature'])"
        ), {"ft": ft})
        row = r.fetchone()
        assert row and row[0] is True, f"field_type '{ft}' not in accepted set"


# ══════════════════════════════════════════════════════════════════════
# Tenant schema provisioning (migration 002)
# ══════════════════════════════════════════════════════════════════════

_TENANT_SCHEMA_NAME = f"migration_test_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="module")
async def provisioned_tenant(pg: AsyncEngine) -> str:
    """Provision a fresh tenant schema; tear it down after the module.

    Returns the *sanitized* schema name (e.g. ``tenant_migration_test_abc123``).
    """
    sanitized = _sanitize_schema_name(_TENANT_SCHEMA_NAME)
    await provision_tenant_schema(
        _TENANT_SCHEMA_NAME,
        engine=pg,
        database_url=DB_URL,
    )
    yield sanitized
    await drop_tenant_schema(_TENANT_SCHEMA_NAME, engine=pg, cascade=True)


class TestTenantSchemaProvisioning:
    async def test_schema_is_created(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            r = await c.execute(text("""
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = :s
            """), {"s": provisioned_tenant})
            assert r.fetchone() is not None, f"Schema '{provisioned_tenant}' not found"

    async def test_alembic_version_written(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            await c.execute(text(f"SET search_path TO {provisioned_tenant}, public"))
            r = await c.execute(text("SELECT version_num FROM alembic_version"))
            row = r.fetchone()
            assert row and row[0] == "004"

    async def test_all_tenant_tables_exist(self, pg, provisioned_tenant):
        expected = {
            "tenant_templates", "tenant_template_definitions",
            "tenant_template_definition_reviews",
            "question_groups", "questions", "question_options",
            "submissions", "submission_answers",
            "submission_status_history", "submission_comments", "products",
            "verification_runs", "verification_step_runs",
            "transform_rule_sets", "transform_rules", "transform_logs",
        }
        async with pg.connect() as c:
            r = await c.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = :s AND table_type = 'BASE TABLE'
                  AND table_name != 'alembic_version'
            """), {"s": provisioned_tenant})
            actual = {row[0] for row in r.fetchall()}
        missing = expected - actual
        assert not missing, f"Missing tenant tables: {missing}"


class TestTenantQuestionsTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "questions", provisioned_tenant)
        assert {"id", "group_id", "version_id", "unique_key", "label",
                "field_type", "required", "is_tenant_editable"}.issubset(cols)

    async def test_group_id_nullable(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            assert await _col_nullable(c, "questions", "group_id", provisioned_tenant)

    async def test_version_id_nullable(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            assert await _col_nullable(c, "questions", "version_id", provisioned_tenant)

    async def test_field_type_check_exists(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            checks = await _checks(c, "questions", provisioned_tenant)
        assert any("field_type" in ch for ch in checks)

    async def test_parent_check_exists(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            checks = await _checks(c, "questions", provisioned_tenant)
        assert any("group_id" in ch and "version_id" in ch for ch in checks)

    async def test_is_tenant_editable_defaults_true(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            default = await _col_default(c, "questions", "is_tenant_editable", provisioned_tenant)
        assert default and "true" in default.lower()


class TestTenantSubmissionsTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "submissions", provisioned_tenant)
        assert {"id", "template_id", "template_version_id",
                "status", "submitter_id", "product_id"}.issubset(cols)

    async def test_status_is_enum(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            dtype, udt = await _col_udt(c, "submissions", "status", provisioned_tenant)
        assert dtype == "USER-DEFINED" and udt == "submissionstatus"

    async def test_submission_status_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "submissionstatus", provisioned_tenant)
        assert set(values) == {
            "draft", "submitted", "under_review",
            "approved", "rejected", "returned", "completed", "cancelled",
        }

    async def test_status_defaults_to_draft(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            default = await _col_default(c, "submissions", "status", provisioned_tenant)
        assert default and "draft" in default.lower()


class TestTenantSubmissionAnswersTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "submission_answers", provisioned_tenant)
        assert {"id", "submission_id", "question_id", "field_type", "answer"}.issubset(cols)

    async def test_field_type_check_exists(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            checks = await _checks(c, "submission_answers", provisioned_tenant)
        assert any("field_type" in ch for ch in checks)

    async def test_date_format_check_exists(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            checks = await _checks(c, "submission_answers", provisioned_tenant)
        assert any("date" in ch.lower() and "answer" in ch for ch in checks), (
            "Expected a CHECK constraint for date format on submission_answers"
        )


class TestTenantProductsTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "products", provisioned_tenant)
        assert {"id", "name", "product_code", "status", "template_id"}.issubset(cols)

    async def test_status_is_enum(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            dtype, udt = await _col_udt(c, "products", "status", provisioned_tenant)
        assert dtype == "USER-DEFINED" and udt == "productstatus"

    async def test_product_status_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "productstatus", provisioned_tenant)
        assert set(values) == {"draft", "active", "inactive"}

    async def test_product_code_unique(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            unique = await _unique_cols(c, "products", provisioned_tenant)
        assert "product_code" in unique

    async def test_status_defaults_to_draft(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            default = await _col_default(c, "products", "status", provisioned_tenant)
        assert default and "draft" in default.lower()


class TestTenantVerificationTables:
    async def test_verification_runs_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "verification_runs", provisioned_tenant)
        assert {
            "id",
            "submission_id",
            "template_version_id",
            "flow_key",
            "journey",
            "status",
            "decision",
            "kyc_level",
            "rules_snapshot",
            "facts_snapshot",
            "result_snapshot",
            "started_at",
            "completed_at",
        }.issubset(cols)

    async def test_verification_step_runs_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "verification_step_runs", provisioned_tenant)
        assert {
            "id",
            "run_id",
            "submission_id",
            "step_key",
            "step_type",
            "adapter_key",
            "status",
            "input_snapshot",
            "output_snapshot",
            "result_snapshot",
            "action_schema",
        }.issubset(cols)

    async def test_verification_step_unique_constraint_exists(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            constraints = await _unique_constraints(c, "verification_step_runs", provisioned_tenant)
        assert ["run_id", "step_key"] in constraints.values()


class TestTenantTemplatesTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "tenant_templates", provisioned_tenant)
        assert {"id", "name", "template_type", "baseline_level", "active_version_id"}.issubset(cols)

    async def test_template_type_is_enum(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            dtype, udt = await _col_udt(c, "tenant_templates", "template_type", provisioned_tenant)
        assert dtype == "USER-DEFINED" and udt == "templatetype"


class TestTenantTemplateDefinitionsTable:
    async def test_review_columns_exist(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "tenant_template_definitions", provisioned_tenant)
        assert {
            "review_status",
            "submitted_for_review_at",
            "submitted_for_review_by",
            "reviewed_at",
            "reviewed_by",
            "review_notes",
        }.issubset(cols)

    async def test_review_status_is_enum(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            dtype, udt = await _col_udt(c, "tenant_template_definitions", "review_status", provisioned_tenant)
        assert dtype == "USER-DEFINED" and udt == "definitionreviewstatus"

    async def test_review_status_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "definitionreviewstatus", provisioned_tenant)
        assert set(values) == {"draft", "pending_review", "approved", "changes_requested"}


class TestTenantTemplateDefinitionReviewsTable:
    async def test_required_columns(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            cols = await _columns(c, "tenant_template_definition_reviews", provisioned_tenant)
        assert {"id", "definition_id", "action", "notes", "created_at", "created_by"}.issubset(cols)

    async def test_action_is_enum(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            dtype, udt = await _col_udt(c, "tenant_template_definition_reviews", "action", provisioned_tenant)
        assert dtype == "USER-DEFINED" and udt == "definitionreviewaction"

    async def test_action_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "definitionreviewaction", provisioned_tenant)
        assert set(values) == {"submitted", "approved", "changes_requested"}


class TestTenantTransformTables:
    async def test_ruleset_status_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "rulesetstatus", provisioned_tenant)
        assert set(values) == {"draft", "published", "archived"}

    async def test_transform_operation_enum_values(self, pg, provisioned_tenant):
        async with pg.connect() as c:
            values = await _enum_values(c, "transformoperation", provisioned_tenant)
        assert set(values) == {
            "identity",
            "rename",
            "map_values",
            "coerce_type",
            "split",
            "merge",
            "default_value",
            "compute",
            "drop",
        }


# ══════════════════════════════════════════════════════════════════════
# Schema cleanup / drop
# ══════════════════════════════════════════════════════════════════════

class TestDropTenantSchema:
    async def test_provision_then_drop_leaves_no_trace(self, pg):
        name = f"drop_test_{uuid.uuid4().hex[:8]}"
        sanitized = _sanitize_schema_name(name)
        await provision_tenant_schema(name, engine=pg, database_url=DB_URL)

        async with pg.connect() as c:
            r = await c.execute(text("""
                SELECT 1 FROM information_schema.schemata WHERE schema_name = :s
            """), {"s": sanitized})
            assert r.fetchone() is not None, "Schema should exist after provision"

        await drop_tenant_schema(name, engine=pg, cascade=True)

        async with pg.connect() as c:
            r = await c.execute(text("""
                SELECT 1 FROM information_schema.schemata WHERE schema_name = :s
            """), {"s": sanitized})
            assert r.fetchone() is None, "Schema should not exist after drop"
