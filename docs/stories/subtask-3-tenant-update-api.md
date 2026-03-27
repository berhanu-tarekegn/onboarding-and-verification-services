# Subtask 3: Tenant Update API with Immutability Protection

## Overview
Develop a secure API endpoint that allows Platform Admins to update tenant editable fields while enforcing immutability constraints on critical attributes (TenantID, slug) and maintaining comprehensive audit trails.

## User Story
As a Platform Admin
I want to update tenant information through a secure API
So that I can maintain accurate tenant metadata while preventing accidental modification of immutable identifiers

---

## Acceptance Criteria

### AC1: Platform Admin Authorization
**Given** the tenant update endpoint exists  
**When** a request is received  
**Then**:
- Only users with `PlatformAdmin` role can access the endpoint
- Non-admin users receive `403 Forbidden`
- Unauthenticated requests receive `401 Unauthorized`

**Verification:**
```python
# Unauthenticated request
PATCH /tenants/{tenant_id} (no auth header)
Response: 401 Unauthorized

# Regular user (not admin)
PATCH /tenants/{tenant_id} (with user token)
Response: 403 Forbidden

# Platform Admin
PATCH /tenants/{tenant_id} (with admin token)
Response: 200 OK
```

---

### AC2: Immutable TenantID Protection
**Given** a tenant exists with TenantID `abc-123`  
**When** an admin attempts to update the TenantID  
**Then**:
- The API rejects the request with `400 Bad Request`
- Error message states: "TenantID is immutable and cannot be modified"
- The TenantID in the URL path is used to identify the tenant (not from request body)
- Request body containing `id` field is ignored or rejected

**Verification:**
```python
# Attempt to change ID in request body
PATCH /tenants/abc-123
{
    "id": "xyz-789",  # Should be rejected or ignored
    "name": "Updated Name"
}
Response: 400 Bad Request
{
    "detail": "TenantID is immutable and cannot be modified"
}

# Or ignore ID field entirely
PATCH /tenants/abc-123
{
    "id": "xyz-789",
    "name": "Updated Name"
}
Response: 200 OK
# ID remains abc-123, only name is updated
```

---

### AC3: Immutable Slug Protection
**Given** a tenant exists with slug `acme`  
**When** an admin attempts to update the slug  
**Then**:
- The API rejects the request with `400 Bad Request`
- Error message states: "Cannot change tenant slug after creation."
- Explanation: Slug is tied to PostgreSQL schema name and cannot be modified

**Verification:**
```python
PATCH /tenants/{tenant_id}
{
    "slug": "new-slug"  # Attempt to change slug
}
Response: 400 Bad Request
{
    "detail": "Cannot change tenant slug after creation."
}
```

---

### AC4: Editable Field Updates
**Given** a tenant exists  
**When** an admin updates editable fields  
**Then** the following fields can be modified:

| Field | Type | Validation | Notes |
|-------|------|------------|-------|
| `name` | String | Max 255 chars, not empty | Tenant display name |
| `is_active` | Boolean | true/false | Legacy status flag |

**And** partial updates are supported (only provided fields are updated)  
**And** omitted fields remain unchanged

**Verification:**
```python
# Update only name
PATCH /tenants/{tenant_id}
{
    "name": "Acme Corporation Ltd"
}
Response: 200 OK
# name updated, is_active unchanged

# Update only is_active
PATCH /tenants/{tenant_id}
{
    "is_active": false
}
Response: 200 OK
# is_active updated, name unchanged

# Update both fields
PATCH /tenants/{tenant_id}
{
    "name": "New Name",
    "is_active": true
}
Response: 200 OK
# Both fields updated
```

---

### AC5: Tenant Existence Validation
**Given** a tenant update request  
**When** the specified TenantID does not exist  
**Then**:
- The API returns `404 Not Found`
- Error message states: "Tenant not found."

**Verification:**
```python
PATCH /tenants/non-existent-id
{
    "name": "Updated Name"
}
Response: 404 Not Found
{
    "detail": "Tenant not found."
}
```

---

### AC6: Request Validation
**Given** a tenant update request  
**When** the request body is validated  
**Then** it enforces:

| Validation | Rule | Error Response |
|------------|------|----------------|
| Name length | Max 255 characters | 422 Unprocessable Entity |
| Name format | Not empty after trimming | 422 Unprocessable Entity |
| is_active type | Boolean only | 422 Unprocessable Entity |
| Unknown fields | Rejected or ignored | 422 or silently ignore based on config |

**Verification:**
```python
# Invalid name (empty)
PATCH /tenants/{tenant_id}
{
    "name": "   "  # Only whitespace
}
Response: 422 Unprocessable Entity

# Invalid name (too long)
PATCH /tenants/{tenant_id}
{
    "name": "a" * 256  # Exceeds 255 chars
}
Response: 422 Unprocessable Entity

# Invalid is_active type
PATCH /tenants/{tenant_id}
{
    "is_active": "yes"  # Should be boolean
}
Response: 422 Unprocessable Entity
```

---

### AC7: Audit Trail Updates
**Given** a tenant is successfully updated  
**When** the update transaction commits  
**Then**:
- `updated_at` is set to current UTC timestamp
- `updated_by` is set to the authenticated admin's identifier
- `created_at` and `created_by` remain unchanged
- All changes are logged to the audit system

**Verification:**
```python
tenant = await get_tenant(tenant_id)
original_updated_at = tenant.updated_at

await update_tenant(tenant_id, {"name": "New Name"})

tenant = await get_tenant(tenant_id)
assert tenant.updated_at > original_updated_at
assert tenant.updated_by == "admin@platform.com"
assert tenant.created_at == original_created_at  # Unchanged
```

---

### AC8: Change Logging
**Given** any tenant update attempt (successful or failed)  
**When** the operation completes  
**Then** the system logs:

**Success Log:**
```json
{
    "event": "tenant.updated",
    "timestamp": "2026-03-06T10:45:30.123Z",
    "actor": "admin@platform.com",
    "tenant_id": "abc-123",
    "tenant_slug": "acme",
    "changes": {
        "name": {
            "old": "Acme Corp",
            "new": "Acme Corporation"
        }
    },
    "ip_address": "192.168.1.100",
    "user_agent": "Mozilla/5.0...",
    "status": "success"
}
```

**Failure Log (Immutability Violation):**
```json
{
    "event": "tenant.update_rejected",
    "timestamp": "2026-03-06T10:45:30.123Z",
    "actor": "admin@platform.com",
    "tenant_id": "abc-123",
    "reason": "Attempted to modify immutable field: slug",
    "attempted_changes": {
        "slug": "new-slug"
    },
    "error_code": 400,
    "ip_address": "192.168.1.100",
    "status": "failure"
}
```

**Verification:**
- Logs include before/after values for changed fields
- Logs exclude sensitive data
- Logs are searchable by tenant_id, actor, event type

---

### AC9: API Response Format
**Given** a tenant is successfully updated  
**When** the API responds  
**Then** it returns:
- HTTP status code `200 OK`
- Response body containing the complete updated tenant record with all fields

**Response Example:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
    "id": "abc-123",
    "name": "Acme Corporation Ltd",
    "slug": "acme",
    "is_active": true,
    "created_at": "2026-03-01T08:00:00.000000Z",
    "updated_at": "2026-03-06T10:45:30.123456Z",
    "created_by": "admin@platform.com",
    "updated_by": "admin@platform.com"
}
```

---

### AC10: Optimistic Locking (Optional)
**Given** multiple admins may update the same tenant concurrently  
**When** concurrent updates occur  
**Then**:
- Last write wins (default behavior without optimistic locking)
- **OR** implement version-based optimistic locking:
  - Each tenant has a `version` field (integer)
  - Update request includes expected version
  - Update fails with `409 Conflict` if version doesn't match

**Note:** This is optional and can be deferred to future enhancement.

---

### AC11: Idempotency
**Given** an admin submits the same update request multiple times  
**When** the requests are processed  
**Then**:
- All requests succeed with `200 OK` (idempotent)
- Final state matches the requested state
- No error for "no changes" scenario

**Verification:**
```python
# Submit same update twice
PATCH /tenants/{tenant_id}
{
    "name": "Same Name"
}
Response: 200 OK

PATCH /tenants/{tenant_id}
{
    "name": "Same Name"
}
Response: 200 OK  # Still succeeds, no error
```

---

### AC12: Transaction Integrity
**Given** a tenant update involves database modifications  
**When** any part of the update fails  
**Then**:
- All changes are rolled back
- Tenant remains in its previous state
- Client receives appropriate error message

**Verification:**
```python
# Simulate validation failure during update
def test_rollback_on_validation_failure():
    tenant = await get_tenant(tenant_id)
    original_name = tenant.name
    
    with pytest.raises(ValidationError):
        await update_tenant(tenant_id, {"name": ""})  # Empty name
    
    tenant = await get_tenant(tenant_id)
    assert tenant.name == original_name  # Unchanged
```

---

## Technical Implementation Notes

### Current Implementation Status
✅ **Already Implemented:**
- API endpoint `PATCH /tenants/{tenant_id}`
- Slug immutability protection
- Partial update support
- Basic request validation

⚠️ **Needs Enhancement:**
- Platform Admin authorization check
- TenantID immutability enforcement (reject ID in body)
- Change logging with before/after values
- Enhanced validation error messages
- Optimistic locking (optional)

---

### API Endpoint Specification

**Endpoint:** `PATCH /tenants/{tenant_id}`

**Request:**
```http
PATCH /tenants/abc-123 HTTP/1.1
Host: api.platform.example.com
Authorization: Bearer {admin_token}
Content-Type: application/json

{
    "name": "Acme Corporation Ltd",
    "is_active": false
}
```

**Success Response:**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
    "id": "abc-123",
    "name": "Acme Corporation Ltd",
    "slug": "acme",
    "is_active": false,
    "created_at": "2026-03-01T08:00:00.000000Z",
    "updated_at": "2026-03-06T10:45:30.123456Z",
    "created_by": "admin@platform.com",
    "updated_by": "admin@platform.com"
}
```

**Error Responses:**

| Status Code | Scenario | Response Body |
|-------------|----------|---------------|
| 400 Bad Request | Immutability violation | `{"detail": "Cannot change tenant slug after creation."}` |
| 401 Unauthorized | Missing/invalid token | `{"detail": "Not authenticated"}` |
| 403 Forbidden | Not a Platform Admin | `{"detail": "Only Platform Admins can update tenants"}` |
| 404 Not Found | Tenant not found | `{"detail": "Tenant not found."}` |
| 422 Unprocessable Entity | Validation error | `{"detail": [{"loc": ["body", "name"], "msg": "..."}]}` |
| 500 Internal Server Error | Server error | `{"detail": "Internal server error"}` |

---

### Service Layer Implementation

```python
# app/services/tenants/tenant.py

async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    session: AsyncSession,
    actor: str | None = None,  # For audit logging
) -> Tenant:
    """Partially update a tenant.
    
    Protected fields:
    - id (TenantID) - immutable
    - slug - immutable (tied to PostgreSQL schema)
    - created_at, created_by - immutable
    
    Editable fields:
    - name
    - is_active
    
    Raises:
        HTTPException 404: Tenant not found
        HTTPException 400: Attempted to modify immutable field
        HTTPException 422: Validation error
    """
    tenant = await get_tenant(tenant_id, session)
    updates = data.model_dump(exclude_unset=True)
    
    # Capture original values for audit log
    changes = {}
    
    # Prevent slug changes
    if "slug" in updates and updates["slug"] != tenant.slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change tenant slug after creation.",
        )
    
    # Prevent ID changes
    if "id" in updates and updates["id"] != tenant.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TenantID is immutable and cannot be modified",
        )
    
    # Apply updates and track changes
    for key, value in updates.items():
        old_value = getattr(tenant, key)
        if old_value != value:
            changes[key] = {"old": old_value, "new": value}
            setattr(tenant, key, value)
    
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    
    # Log changes
    if changes:
        await log_audit_event(
            event="tenant.updated",
            actor=actor,
            tenant_id=tenant.id,
            changes=changes,
        )
    
    return tenant
```

---

## Definition of Done

### Checklist
- [ ] API endpoint `PATCH /tenants/{tenant_id}` implemented
- [ ] Platform Admin authorization enforced (403 for non-admins)
- [ ] TenantID immutability protected (reject ID in body)
- [ ] Slug immutability protected (400 on attempt to change)
- [ ] Partial update support (only provided fields updated)
- [ ] Name validation (max 255 chars, not empty)
- [ ] is_active validation (boolean only)
- [ ] Tenant existence check (404 if not found)
- [ ] Audit fields auto-updated (updated_at, updated_by)
- [ ] Change logging with before/after values
- [ ] Idempotency support (no error for unchanged values)
- [ ] Transaction rollback on failure
- [ ] Unit tests for service layer (90%+ coverage)
- [ ] Integration tests for API endpoint
- [ ] Error handling tests (400, 403, 404, 422)
- [ ] Concurrent update test
- [ ] API documentation (OpenAPI/Swagger) updated

---

## Test Plan

### Unit Tests

```python
# tests/unit/services/test_tenant_update.py

async def test_update_tenant_name():
    """Verify tenant name can be updated."""
    tenant = await create_tenant(...)
    updated = await update_tenant(
        tenant.id,
        TenantUpdate(name="New Name"),
        session
    )
    assert updated.name == "New Name"
    assert updated.slug == tenant.slug  # Unchanged

async def test_update_tenant_slug_rejected():
    """Verify slug update is rejected."""
    tenant = await create_tenant(...)
    
    with pytest.raises(HTTPException) as exc:
        await update_tenant(
            tenant.id,
            TenantUpdate(slug="new-slug"),
            session
        )
    
    assert exc.value.status_code == 400
    assert "Cannot change tenant slug" in exc.value.detail

async def test_update_tenant_not_found():
    """Verify 404 for non-existent tenant."""
    with pytest.raises(HTTPException) as exc:
        await update_tenant(
            uuid4(),
            TenantUpdate(name="Test"),
            session
        )
    
    assert exc.value.status_code == 404

async def test_update_audit_fields():
    """Verify audit fields are updated."""
    tenant = await create_tenant(...)
    original_updated_at = tenant.updated_at
    
    await asyncio.sleep(0.1)  # Ensure time difference
    
    updated = await update_tenant(
        tenant.id,
        TenantUpdate(name="New Name"),
        session,
        actor="admin2@platform.com"
    )
    
    assert updated.updated_at > original_updated_at
    assert updated.updated_by == "admin2@platform.com"
    assert updated.created_at == tenant.created_at  # Unchanged
```

### Integration Tests

```python
# tests/integration/test_tenant_update_api.py

async def test_update_tenant_endpoint_success(client, admin_token):
    """Test successful tenant update via API."""
    # Create tenant
    create_response = await client.post(
        "/tenants",
        json={"name": "Test", "slug": "test"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    tenant_id = create_response.json()["id"]
    
    # Update tenant
    update_response = await client.patch(
        f"/tenants/{tenant_id}",
        json={"name": "Updated Name"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated Name"
    assert update_response.json()["slug"] == "test"  # Unchanged

async def test_update_tenant_requires_admin(client, user_token):
    """Test non-admin cannot update tenant."""
    response = await client.patch(
        "/tenants/some-id",
        json={"name": "Test"},
        headers={"Authorization": f"Bearer {user_token}"}
    )
    
    assert response.status_code == 403

async def test_update_tenant_immutability(client, admin_token):
    """Test immutability protection."""
    # Create tenant
    create_response = await client.post(
        "/tenants",
        json={"name": "Test", "slug": "test"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    tenant_id = create_response.json()["id"]
    
    # Attempt slug update
    response = await client.patch(
        f"/tenants/{tenant_id}",
        json={"slug": "new-slug"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    
    assert response.status_code == 400
    assert "Cannot change tenant slug" in response.json()["detail"]
```

---

## Dependencies
- `app/models/public/tenant.py` - Tenant model
- `app/schemas/tenants/tenant.py` - TenantUpdate schema
- `app/services/tenants/tenant.py` - Tenant service logic
- `fastapi` - API framework
- `sqlmodel` - ORM

---

## Future Enhancements
1. Optimistic locking with version field
2. Partial field validation (e.g., validate name format beyond length)
3. Tenant update webhooks
4. Bulk tenant update endpoint
5. Field-level permissions (different admins can update different fields)
6. Tenant update approval workflow for critical changes

---

## References
- [Subtask 1: Tenant Entity Structure](./subtask-1-tenant-entity-structure.md)
- [Subtask 2: Tenant Creation API](./subtask-2-tenant-creation-api.md)
- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
