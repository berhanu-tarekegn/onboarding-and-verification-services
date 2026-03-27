"""Tests — Products (Module 4).

Covers:
* Create / list / get / update / delete
* Lifecycle: DRAFT → ACTIVE → INACTIVE → ACTIVE
* Activation blocked without template_id
* product_code and template_id immutable while ACTIVE
* Delete blocked for ACTIVE products
* KYC config endpoint: active-only, returns question_groups
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import make_product_payload


# ── Create ────────────────────────────────────────────────────────────

class TestCreateProduct:
    async def test_create_returns_201_in_draft(
        self, tenant_client: AsyncClient, published_tenant_template: dict
    ):
        r = await tenant_client.post(
            "/api/v1/products",
            json=make_product_payload(template_id=published_tenant_template["id"]),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "draft"
        assert body["version"] == 1

    async def test_create_without_template_id(self, tenant_client: AsyncClient):
        r = await tenant_client.post(
            "/api/v1/products",
            json={"name": "No Template", "product_code": "NT-001"},
        )
        assert r.status_code == 201
        assert r.json()["template_id"] is None

    async def test_duplicate_product_code_rejected(
        self, tenant_client: AsyncClient, published_tenant_template: dict
    ):
        payload = make_product_payload(template_id=published_tenant_template["id"])
        await tenant_client.post("/api/v1/products", json=payload)
        r2 = await tenant_client.post("/api/v1/products", json=payload)
        assert r2.status_code in (409, 422)

    async def test_missing_product_code_rejected(self, tenant_client: AsyncClient):
        r = await tenant_client.post("/api/v1/products", json={"name": "No Code"})
        assert r.status_code == 422


# ── List / Get ────────────────────────────────────────────────────────

class TestListGetProduct:
    async def test_list_returns_products(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get("/api/v1/products")
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert active_product["id"] in ids

    async def test_list_filter_by_status(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get("/api/v1/products", params={"status": "active"})
        assert r.status_code == 200
        for p in r.json():
            assert p["status"] == "active"

    async def test_get_product_by_id(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get(f"/api/v1/products/{active_product['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == active_product["id"]

    async def test_get_nonexistent_product_returns_404(
        self, tenant_client: AsyncClient
    ):
        r = await tenant_client.get(
            "/api/v1/products/00000000-0000-0000-0000-000000000000"
        )
        assert r.status_code == 404


# ── Activate ──────────────────────────────────────────────────────────

class TestActivateProduct:
    async def test_activate_with_template_succeeds(
        self, tenant_client: AsyncClient, published_tenant_template: dict
    ):
        r = await tenant_client.post(
            "/api/v1/products",
            json=make_product_payload(template_id=published_tenant_template["id"]),
        )
        pid = r.json()["id"]
        r2 = await tenant_client.post(f"/api/v1/products/{pid}/activate")
        assert r2.status_code == 200
        assert r2.json()["status"] == "active"

    async def test_activate_without_template_rejected(
        self, tenant_client: AsyncClient
    ):
        r = await tenant_client.post(
            "/api/v1/products",
            json={"name": "No Template", "product_code": "NT-002"},
        )
        pid = r.json()["id"]
        r2 = await tenant_client.post(f"/api/v1/products/{pid}/activate")
        assert r2.status_code == 422

    async def test_activate_already_active_product_rejected(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.post(f"/api/v1/products/{active_product['id']}/activate")
        assert r.status_code in (409, 422)


# ── Update restrictions while ACTIVE ──────────────────────────────────

class TestProductUpdateRestrictions:
    async def test_update_name_while_active_allowed(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.patch(
            f"/api/v1/products/{active_product['id']}",
            json={"name": "Updated Name"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Name"

    async def test_update_product_code_while_active_rejected(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.patch(
            f"/api/v1/products/{active_product['id']}",
            json={"product_code": "CHANGED-001"},
        )
        assert r.status_code == 422

    async def test_update_template_id_while_active_rejected(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.patch(
            f"/api/v1/products/{active_product['id']}",
            json={"template_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert r.status_code == 422


# ── Deactivate ────────────────────────────────────────────────────────

class TestDeactivateProduct:
    async def test_deactivate_active_product(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.post(
            f"/api/v1/products/{active_product['id']}/deactivate"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "inactive"

    async def test_reactivate_inactive_product(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        await tenant_client.post(
            f"/api/v1/products/{active_product['id']}/deactivate"
        )
        r = await tenant_client.post(
            f"/api/v1/products/{active_product['id']}/activate"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"


# ── Delete ────────────────────────────────────────────────────────────

class TestDeleteProduct:
    async def test_delete_draft_product_succeeds(
        self, tenant_client: AsyncClient, published_tenant_template: dict
    ):
        r = await tenant_client.post(
            "/api/v1/products",
            json=make_product_payload(
                template_id=published_tenant_template["id"]
            ),
        )
        pid = r.json()["id"]
        r2 = await tenant_client.delete(f"/api/v1/products/{pid}")
        assert r2.status_code in (200, 204)

    async def test_delete_active_product_rejected(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.delete(f"/api/v1/products/{active_product['id']}")
        assert r.status_code in (403, 422)


# ── KYC Config ────────────────────────────────────────────────────────

class TestProductKycConfig:
    async def test_kyc_config_returns_question_groups(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get(
            f"/api/v1/products/{active_product['id']}/kyc-config"
        )
        assert r.status_code == 200
        body = r.json()
        assert "question_groups" in body
        assert isinstance(body["question_groups"], list)
        assert len(body["question_groups"]) >= 1

    async def test_kyc_config_returns_rules_config(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get(
            f"/api/v1/products/{active_product['id']}/kyc-config"
        )
        assert r.status_code == 200
        assert "rules_config" in r.json()

    async def test_kyc_config_returns_baseline_version_info(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        r = await tenant_client.get(
            f"/api/v1/products/{active_product['id']}/kyc-config"
        )
        body = r.json()
        assert "baseline_version_id" in body
        assert "baseline_version_tag" in body

    async def test_kyc_config_unavailable_for_inactive_product(
        self, tenant_client: AsyncClient, active_product: dict
    ):
        await tenant_client.post(
            f"/api/v1/products/{active_product['id']}/deactivate"
        )
        r = await tenant_client.get(
            f"/api/v1/products/{active_product['id']}/kyc-config"
        )
        assert r.status_code == 422

    async def test_kyc_config_unavailable_for_draft_product(
        self, tenant_client: AsyncClient, published_tenant_template: dict
    ):
        r = await tenant_client.post(
            "/api/v1/products",
            json=make_product_payload(template_id=published_tenant_template["id"]),
        )
        pid = r.json()["id"]
        r2 = await tenant_client.get(f"/api/v1/products/{pid}/kyc-config")
        assert r2.status_code == 422
