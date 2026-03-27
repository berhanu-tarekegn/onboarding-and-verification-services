import asyncio
import httpx
import sys

BASE_URL = "http://localhost:7090/api/v1"
TENANT_SLUG = "test-bank"
USER_ID = "user-123"


async def verify_onboarding():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"--- Phase 1 Verification for Tenant: {TENANT_SLUG} ---")

        # 1. Register Tenant (if not exists)
        print("\n1. Registering tenant...")
        try:
            resp = await client.post(
                f"{BASE_URL}/tenants", json={"name": "Test Bank", "slug": TENANT_SLUG}
            )
            if resp.status_code == 201:
                print(f"   Success: Tenant registered.")
            elif resp.status_code == 400 and "already exists" in resp.text:
                print(f"   Note: Tenant already exists.")
            else:
                print(f"   Status: {resp.status_code}, Body: {resp.text}")
        except Exception as e:
            print(f"   Error registering tenant: {e}")

        # 2. Setup Template & Active Version
        print("\n2. Setting up Onboarding Template...")
        headers = {"X-Tenant-ID": TENANT_SLUG}
        template_id = None
        try:
            # Check if template already exists
            resp = await client.get(f"{BASE_URL}/templates", headers=headers)
            templates = resp.json()
            onboarding_tpl = next(
                (t for t in templates if t["name"] == "Onboarding"), None
            )

            if onboarding_tpl:
                template_id = onboarding_tpl["id"]
                print(
                    f"   Note: Template 'Onboarding' already exists (ID: {template_id})"
                )
            else:
                # Create template with initial version
                resp = await client.post(
                    f"{BASE_URL}/templates",
                    headers=headers,
                    json={
                        "name": "Onboarding",
                        "description": "Standard Customer Onboarding",
                        "initial_version": {
                            "version_tag": "v1.0.0",
                            "form_schema": {
                                "steps": [
                                    {
                                        "name": "Identity",
                                        "fields": [
                                            {"name": "full_name", "type": "text"}
                                        ],
                                    }
                                ]
                            },
                            "rules_config": {"auto_approve": True},
                        },
                    },
                )
                if resp.status_code == 201:
                    template_id = resp.json()["id"]
                    print(f"   Success: Template created (ID: {template_id})")
                else:
                    print(f"   Error creating template: {resp.text}")
                    return
        except Exception as e:
            print(f"   Error setting up template: {e}")
            return

        # 3. Initialize Onboarding
        print("\n3. Initializing Onboarding workflow...")
        workflow_id = None
        try:
            resp = await client.post(
                f"{BASE_URL}/onboarding/init",
                headers=headers,
                json={"bank_id": TENANT_SLUG, "user_id": USER_ID},
            )
            if resp.status_code == 200:
                data = resp.json()
                workflow_id = data["workflow_id"]
                print(f"   Success: Workflow started (ID: {workflow_id})")
            else:
                print(f"   Error initializing onboarding: {resp.text}")
                return
        except Exception as e:
            print(f"   Error initializing: {e}")
            return

        # 4. Check Status (should be waiting for data)
        print("\n4. Checking initial workflow status...")
        try:
            resp = await client.get(
                f"{BASE_URL}/onboarding/status/{workflow_id}", headers=headers
            )
            print(f"   Status: {resp.json().get('status')}")
        except Exception as e:
            print(f"   Error checking status: {e}")

        # 5. Submit Data
        print("\n5. Submitting user data...")
        try:
            resp = await client.post(
                f"{BASE_URL}/onboarding/submit",
                headers=headers,
                json={
                    "workflow_id": workflow_id,
                    "data": {"full_name": "John Doe", "user_id": USER_ID},
                },
            )
            if resp.status_code == 200:
                print("   Success: Data submitted.")
            else:
                print(f"   Error submitting data: {resp.text}")
                return
        except Exception as e:
            print(f"   Error submitting: {e}")
            return

        # 6. Poll for Completion
        print("\n6. Polling for workflow completion...")
        for _ in range(10):
            await asyncio.sleep(2)
            resp = await client.get(
                f"{BASE_URL}/onboarding/status/{workflow_id}", headers=headers
            )
            status_data = resp.json()
            status = status_data.get("status")
            print(f"   Current Status: {status}")
            if status in ["completed", "rejected"]:
                print(f"   Final Message: {status_data.get('message')}")
                break
        else:
            print("   Timed out waiting for workflow completion.")


if __name__ == "__main__":
    asyncio.run(verify_onboarding())
