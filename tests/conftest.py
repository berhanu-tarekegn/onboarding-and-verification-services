"""Shared test fixtures and helpers.

Strategy
--------
* Uses a real disposable PostgreSQL test database supplied via
  DATABASE_TEST_URL.
* Each non-migration test starts from a clean database state.
* An `AsyncClient` wired directly to the ASGI app is used instead of a live
  server, making tests fast and deterministic.

Environment variable
--------------------
Set DATABASE_TEST_URL to point at your disposable test database:
    export DATABASE_TEST_URL="postgresql+asyncpg://onboarding:onboarding@localhost:5433/onboarding_test_db"

The test database must exist and have the current Alembic head applied before
running the suite:
    alembic upgrade head
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings

# Default: run integration tests with auth disabled unless a test explicitly
# enables it (auth-specific tests set env vars and clear the settings cache).
os.environ["AUTH_ENABLED"] = "false"
os.environ["JWKS_REQUIRED"] = "false"
get_settings.cache_clear()

from app.main import app

# ── Test database URL ─────────────────────────────────────────────────

_UNIT_MODULES = {
    "tests/test_answer_validator.py",
    "tests/test_submission_validation.py",
    "tests/test_four_eyes.py",
}

_AUTH_MODULES = {
    "tests/test_auth.py",
    "tests/test_auth_proxy.py",
    "tests/test_rbac_routes.py",
    "tests/test_rbac_tenants.py",
}

_API_DB_MODULES = {
    "tests/test_tenants.py",
    "tests/test_baseline_templates.py",
    "tests/test_tenant_templates.py",
    "tests/test_products.py",
    "tests/test_submissions.py",
    "tests/test_submission_search.py",
    "tests/test_submission_verifications.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply coarse-grained suite markers based on test module."""
    for item in items:
        nodeid = item.nodeid.split("::", 1)[0]
        if nodeid in _UNIT_MODULES:
            item.add_marker(pytest.mark.unit)
        if nodeid in _AUTH_MODULES:
            item.add_marker(pytest.mark.auth)
        if nodeid in _API_DB_MODULES:
            item.add_marker(pytest.mark.api)
            item.add_marker(pytest.mark.db)
        if nodeid == "tests/test_database_migrations.py":
            item.add_marker(pytest.mark.db)
            item.add_marker(pytest.mark.api)

def _test_database_url() -> str | None:
    return os.environ.get("DATABASE_TEST_URL")


TEST_DATABASE_URL = _test_database_url()

test_engine = (
    create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )
    if TEST_DATABASE_URL
    else None
)

TestSessionFactory = (
    async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    if test_engine is not None
    else None
)


@pytest_asyncio.fixture(autouse=True)
async def clean_database(request) -> AsyncGenerator[None, None]:
    """Reset public data and tenant schemas between non-migration tests."""
    if "db_migration" in request.keywords:
        yield
        return

    if "db" not in request.keywords:
        yield
        return

    if test_engine is None:
        pytest.skip("Set DATABASE_TEST_URL to a disposable test database before running DB-backed tests.")

    async def _reset() -> None:
        async with test_engine.begin() as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name LIKE 'tenant_%'
                    ORDER BY schema_name
                    """
                )
            )
            for (schema_name,) in rows.fetchall():
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

            await conn.execute(
                text(
                    """
                    TRUNCATE TABLE
                        public.identity_links,
                        public.authz_policies,
                        public.baseline_question_options,
                        public.baseline_questions,
                        public.baseline_question_groups,
                        public.baseline_template_definitions,
                        public.baseline_templates,
                        public.tenants
                    CASCADE
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO public.authz_policies
                        (id, scope, tenant_id, version, policy, created_by, updated_by)
                    VALUES
                        (gen_random_uuid(), 'global', NULL, 1, '{}'::jsonb, 'system', 'system')
                    """
                )
            )

    await _reset()
    yield
    await _reset()


# ── HTTP client fixture ───────────────────────────────────────────────

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Unauthenticated AsyncClient for public endpoints (tenants, baselines)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def tenant_client(client: AsyncClient, tenant: dict) -> AsyncClient:
    """AsyncClient pre-configured with X-Tenant-ID for tenant-scoped endpoints."""
    client.headers.update({"X-Tenant-ID": tenant["id"]})
    return client


# ── Payload builders (pure helpers, no I/O) ──────────────────────────

def make_tenant_payload(tenant_key: str = "test_bank", name: str = "Test Bank") -> dict:
    return {"name": name, "tenant_key": tenant_key}


def make_baseline_payload(
    template_type: str = "kyc",
    level: int = 1,
    name: str = "KYC Standard",
    with_version: bool = True,
) -> dict:
    payload: dict = {
        "name": name,
        "description": "Standard KYC questions",
        "category": "kyc",
        "template_type": template_type,
        "level": level,
    }
    if with_version:
        payload["initial_version"] = {
            "version_tag": "1.0.0",
            "rules_config": {},
            "changelog": "Initial",
            "question_groups": [
                {
                    "unique_key": "personal_info",
                    "title": "Personal Information",
                    "display_order": 1,
                    "questions": [
                        {
                            "unique_key": "full_name",
                            "label": "Full Name",
                            "field_type": "text",
                            "required": True,
                            "display_order": 1,
                        },
                        {
                            "unique_key": "dob",
                            "label": "Date of Birth",
                            "field_type": "date",
                            "required": True,
                            "display_order": 2,
                            "min_date": "1900-01-01",
                            "max_date": "2010-01-01",
                        },
                        {
                            "unique_key": "id_type",
                            "label": "ID Type",
                            "field_type": "dropdown",
                            "required": True,
                            "display_order": 3,
                            "options": [
                                {"value": "national_id", "display_order": 1},
                                {"value": "passport", "display_order": 2},
                            ],
                        },
                    ],
                }
            ],
            "questions": [
                {
                    "unique_key": "consent",
                    "label": "I agree to terms",
                    "field_type": "checkbox",
                    "required": True,
                    "display_order": 99,
                }
            ],
        }
    return payload


def make_tenant_template_payload(
    template_type: str = "kyc",
    baseline_level: int = 1,
) -> dict:
    return {
        "name": "My KYC Flow",
        "description": "Tenant KYC",
        "template_type": template_type,
        "baseline_level": baseline_level,
    }


def make_product_payload(template_id: str | None = None) -> dict:
    p: dict = {"name": "Retail Account", "product_code": "RA-001"}
    if template_id:
        p["template_id"] = template_id
    return p


# ── Reusable object fixtures ──────────────────────────────────────────

@pytest_asyncio.fixture
async def tenant(client: AsyncClient) -> dict:
    """Create a tenant and return the JSON response body."""
    r = await client.post("/api/v1/tenants", json=make_tenant_payload())
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture
async def baseline(client: AsyncClient) -> dict:
    """Create a KYC baseline template (with initial version) and return the body."""
    r = await client.post("/api/v1/baseline-templates", json=make_baseline_payload())
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture
async def published_baseline(client: AsyncClient, baseline: dict) -> dict:
    """Publish the baseline's initial version and return the updated template body."""
    version_id = baseline["active_version_id"] or baseline["versions"][0]["id"]
    r = await client.post(
        f"/api/v1/baseline-templates/{baseline['id']}/definitions/{version_id}/publish",
        params={"set_as_active": "true"},
    )
    assert r.status_code == 200, r.text
    # Return fresh template data
    r2 = await client.get(f"/api/v1/baseline-templates/{baseline['id']}")
    assert r2.status_code == 200
    return r2.json()


@pytest_asyncio.fixture
async def tenant_template(
    tenant_client: AsyncClient,
    published_baseline: dict,
) -> dict:
    """Create a tenant template (baseline already published)."""
    r = await tenant_client.post(
        "/api/v1/templates",
        json=make_tenant_template_payload(),
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest_asyncio.fixture
async def published_tenant_template(
    tenant_client: AsyncClient,
    tenant_template: dict,
) -> dict:
    """Create a version for the tenant template and publish it."""
    # Create definition
    r = await tenant_client.post(
        f"/api/v1/templates/{tenant_template['id']}/definitions",
        json={"version_tag": "1.0.0", "rules_config": {}, "changelog": "v1"},
    )
    assert r.status_code == 201, r.text
    version_id = r.json()["id"]

    # Publish
    r2 = await tenant_client.post(
        f"/api/v1/templates/{tenant_template['id']}/definitions/{version_id}/publish",
        params={"set_as_active": "true"},
    )
    assert r2.status_code == 200, r2.text
    return tenant_template


@pytest_asyncio.fixture
async def active_product(
    tenant_client: AsyncClient,
    published_tenant_template: dict,
) -> dict:
    """Create and activate a product linked to the published tenant template."""
    # Create product with template
    r = await tenant_client.post(
        "/api/v1/products",
        json=make_product_payload(template_id=published_tenant_template["id"]),
    )
    assert r.status_code == 201, r.text
    product_id = r.json()["id"]

    # Activate
    r2 = await tenant_client.post(f"/api/v1/products/{product_id}/activate")
    assert r2.status_code == 200, r2.text
    return r2.json()
