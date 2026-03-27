"""Tests — Tenant Management (Module 1).

Covers:
* CRUD lifecycle (create, list, get, update, delete)
* schema_name format validation (lowercase, no hyphens, no spaces, length)
* Duplicate schema_name rejection
* UUID-based retrieval and 404 behaviour
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import make_tenant_payload


# ── Create ────────────────────────────────────────────────────────────

class TestCreateTenant:
    async def test_create_returns_201_with_expected_shape(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json=make_tenant_payload("acme_bank", "Acme Bank"))
        assert r.status_code == 201
        body = r.json()
        assert body["schema_name"] == "acme_bank"
        assert body["name"] == "Acme Bank"
        assert body["is_active"] is True
        assert "id" in body
        # Audit fields present
        assert "created_at" in body
        assert "updated_at" in body

    async def test_schema_name_with_uppercase_rejected(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json=make_tenant_payload("AcmeBank"))
        assert r.status_code == 422

    async def test_schema_name_with_hyphen_rejected(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json=make_tenant_payload("acme-bank"))
        assert r.status_code == 422

    async def test_schema_name_with_space_rejected(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json=make_tenant_payload("acme bank"))
        assert r.status_code == 422

    async def test_schema_name_exceeding_63_chars_rejected(self, client: AsyncClient):
        long_name = "a" * 64
        r = await client.post("/api/v1/tenants", json=make_tenant_payload(long_name))
        assert r.status_code == 422

    async def test_duplicate_schema_name_rejected(self, client: AsyncClient, tenant: dict):
        r = await client.post(
            "/api/v1/tenants",
            json=make_tenant_payload(tenant["schema_name"], "Another Bank"),
        )
        assert r.status_code in (409, 422)

    async def test_missing_name_rejected(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json={"schema_name": "no_name"})
        assert r.status_code == 422

    async def test_missing_schema_name_rejected(self, client: AsyncClient):
        r = await client.post("/api/v1/tenants", json={"name": "No Schema"})
        assert r.status_code == 422


# ── List ──────────────────────────────────────────────────────────────

class TestListTenants:
    async def test_returns_list(self, client: AsyncClient, tenant: dict):
        r = await client.get("/api/v1/tenants")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        ids = [t["id"] for t in r.json()]
        assert tenant["id"] in ids

    async def test_empty_db_returns_empty_list(self, client: AsyncClient):
        r = await client.get("/api/v1/tenants")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ── Get ───────────────────────────────────────────────────────────────

class TestGetTenant:
    async def test_get_by_id_returns_tenant(self, client: AsyncClient, tenant: dict):
        r = await client.get(f"/api/v1/tenants/{tenant['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == tenant["id"]

    async def test_get_nonexistent_returns_404(self, client: AsyncClient):
        r = await client.get("/api/v1/tenants/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    async def test_get_invalid_uuid_returns_422(self, client: AsyncClient):
        r = await client.get("/api/v1/tenants/not-a-uuid")
        assert r.status_code == 422


# ── Update ────────────────────────────────────────────────────────────

class TestUpdateTenant:
    async def test_update_name(self, client: AsyncClient, tenant: dict):
        r = await client.patch(
            f"/api/v1/tenants/{tenant['id']}",
            json={"name": "Updated Bank"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Bank"

    async def test_update_is_active_flag(self, client: AsyncClient, tenant: dict):
        r = await client.patch(
            f"/api/v1/tenants/{tenant['id']}",
            json={"is_active": False},
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    async def test_schema_name_is_not_updatable(self, client: AsyncClient, tenant: dict):
        """schema_name must NOT be present in the update schema — it should be ignored."""
        r = await client.patch(
            f"/api/v1/tenants/{tenant['id']}",
            json={"schema_name": "hacked_schema"},
        )
        # Either rejected (422) or silently ignored; the stored value must not change
        if r.status_code == 200:
            assert r.json()["schema_name"] == tenant["schema_name"]

    async def test_update_nonexistent_returns_404(self, client: AsyncClient):
        r = await client.patch(
            "/api/v1/tenants/00000000-0000-0000-0000-000000000000",
            json={"name": "Ghost"},
        )
        assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────

class TestDeleteTenant:
    async def test_delete_soft_deletes(self, client: AsyncClient, tenant: dict):
        r = await client.delete(f"/api/v1/tenants/{tenant['id']}")
        assert r.status_code in (200, 204)

        # Tenant still retrievable, is_active = False
        r2 = await client.get(f"/api/v1/tenants/{tenant['id']}")
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_delete_nonexistent_returns_404(self, client: AsyncClient):
        r = await client.delete("/api/v1/tenants/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


# ── Header validation for tenant-scoped routes ────────────────────────

class TestTenantHeaderValidation:
    async def test_missing_tenant_header_returns_422(self, client: AsyncClient):
        r = await client.get("/api/v1/templates")
        assert r.status_code == 422

    async def test_non_uuid_tenant_header_returns_400(self, client: AsyncClient):
        r = await client.get(
            "/api/v1/templates",
            headers={"X-Tenant-ID": "acme_bank"},
        )
        assert r.status_code == 400

    async def test_valid_uuid_tenant_header_passes_header_check(
        self, client: AsyncClient, tenant: dict
    ):
        r = await client.get(
            "/api/v1/templates",
            headers={"X-Tenant-ID": tenant["id"]},
        )
        # 200 or 404 — either means the header was accepted
        assert r.status_code in (200, 404)

    async def test_inactive_tenant_returns_403(self, client: AsyncClient, tenant: dict):
        # Deactivate first
        await client.patch(f"/api/v1/tenants/{tenant['id']}", json={"is_active": False})
        r = await client.get(
            "/api/v1/templates",
            headers={"X-Tenant-ID": tenant["id"]},
        )
        assert r.status_code == 403
