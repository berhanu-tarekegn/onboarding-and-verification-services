# Subtask 2: Develop Tenant Creation API Endpoint

## Overview
Develop a secure API endpoint for Platform Admins to create tenants with auto-generated TenantID, unique slug validation, default configuration initialization, and comprehensive audit logging.

## User Story
As a Platform Admin
I want to create new tenants via a secure API endpoint
So that organizations can be onboarded with proper isolation, validation, and audit trails

---

## Acceptance Criteria

### AC1: Unique TenantID Auto-Generation
**Given** a Platform Admin submits a tenant creation request  
**When** the API processes the request  
**Then** a unique TenantID (UUID7) is automatically generated  
**And** the TenantID is time-ordered and globally unique  
**And** the client does not provide or control the TenantID

**Verification:**
- API does not accept `id` field in request body
- UUID7 is generated server-side using `uuid_extensions.uuid7()`
- Multiple concurrent creations generate unique IDs
- IDs are sortable by creation time

---

### AC2: Tenant Uniqueness Validation
**Given** a Platform Admin attempts to create a tenant  
**When** the system validates the request  
**Then** it ensures:
- The tenant `slug` is unique across all tenants
- The tenant `name` can be duplicated (multiple tenants may have similar names)
- Duplicate slug returns `409 Conflict` error
- Error message clearly indicates the conflict: "Tenant with slug '{slug}' already exists."

**Verification:**
```python
# Test case 1: Successful creation
POST /tenants
{
    "name": "Acme Corp",
    "slug": "acme"
}
Response: 201 Created

# Test case 2: Duplicate slug
POST /tenants
{
    "name": "Acme Corporation",
    "slug": "acme"  # Already exists
}
Response: 409 Conflict
{
    "detail": "Tenant with slug 'acme' already exists."
}
```

---

### AC3: Request Validation
**Given** a Platform Admin sends a tenant creation request  
**When** the API validates the request body  
**Then** it enforces:

| Field | Required | Type | Constraints | Validation |
|-------|----------|------|-------------|------------|
| `name` | Yes | String | Max 255 chars | Not empty, trimmed |
| `slug` | Yes | String | Max 63 chars | Lowercase, alphanumeric + underscore/hyphen, no spaces |

**And** returns `422 Unprocessable Entity` for validation failures  
**And** provides clear field-level error messages

**Verification:**
```python
# Missing required field
POST /tenants
{
    "name": "Test Corp"
    # slug missing
}
Response: 422 Unprocessable Entity

# Invalid slug format
POST /tenants
{
    "name": "Test Corp",
    "slug": "Invalid Slug!"  # Contains space and special char
}
Response: 422 Unprocessable Entity
{
    "detail": [
        {
            "loc": ["body", "slug"],
            "msg": "Slug must be lowercase alphanumeric with hyphens/underscores only",
            "type": "value_error"
        }
    ]
}
```

---

### AC4: Default Configuration Initialization
**Given** a new tenant is successfully created  
**When** the tenant record is persisted  
**Then**:
- `is_active` is set to `true` by default
- Audit fields are auto-populated:
  - `created_at` = current UTC timestamp
  - `updated_at` = current UTC timestamp
  - `created_by` = authenticated admin user
  - `updated_by` = authenticated admin user

**Verification:**
```python
tenant = await create_tenant(...)
assert tenant.is_active == True
assert tenant.created_at is not None
assert tenant.created_by == "admin@platform.com"
assert tenant.updated_at == tenant.created_at
```

---

### AC5: PostgreSQL Schema Provisioning
**Given** a tenant is successfully created in the database  
**When** the creation transaction commits  
**Then**:
- A PostgreSQL schema named `tenant_{slug}` is created
- The schema contains all tenant-specific tables (via migrations)
- Schema provisioning failures rollback the tenant creation
- The tenant record is not committed until schema provisioning succeeds

**Verification:**
```sql
-- After creating tenant with slug "acme"
SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'tenant_acme';
-- Returns: tenant_acme

-- Verify tables exist in schema
SELECT table_name FROM information_schema.tables WHERE table_schema = 'tenant_acme';
-- Returns: templates, submissions, verifications, etc.
```

---

### AC6: Comprehensive Audit Logging
**Given** any tenant creation attempt (successful or failed)  
**When** the operation completes  
**Then** the system logs:

**Success Log:**
```json
{
    "event": "tenant.created",
    "timestamp": "2026-03-06T10:30:45.123Z",
    "actor": "admin@platform.com",
    "tenant_id": "01234567-89ab-cdef-0123-456789abcdef",
    "tenant_slug": "acme",
    "tenant_name": "Acme Corp",
    "ip_address": "192.168.1.100",
    "user_agent": "Mozilla/5.0...",
    "status": "success"
}
```

**Failure Log:**
```json
{
    "event": "tenant.creation_failed",
    "timestamp": "2026-03-06T10:30:45.123Z",
    "actor": "admin@platform.com",
    "tenant_slug": "acme",
    "reason": "Tenant with slug 'acme' already exists.",
    "error_code": 409,
    "ip_address": "192.168.1.100",
    "status": "failure"
}
```

**Verification:**
- Logs are written to centralized logging system (e.g., CloudWatch, ELK)
- Logs include correlation IDs for request tracing
- Sensitive data (passwords, tokens) is never logged

---

### AC7: API Security and Authorization
**Given** the tenant creation endpoint exists  
**When** a request is received  
**Then**:
- Only users with `PlatformAdmin` role can access the endpoint
- Non-admin users receive `403 Forbidden`
- Unauthenticated requests receive `401 Unauthorized`
- Rate limiting is applied (e.g., max 10 tenant creations per minute per admin)

**Verification:**
```python
# Unauthenticated
POST /tenants (no auth header)
Response: 401 Unauthorized

# Regular user (not admin)
POST /tenants (with user token)
Response: 403 Forbidden
{
    "detail": "Only Platform Admins can create tenants."
}

# Platform Admin
POST /tenants (with admin token)
Response: 201 Created
```

---

### AC8: API Response Format
**Given** a tenant is successfully created  
**When** the API responds  
**Then** it returns:
- HTTP status code `201 Created`
- `Location` header with tenant resource URL: `/tenants/{tenant_id}`
- Response body containing the created tenant with all fields

**Response Example:**
```json
HTTP/1.1 201 Created
Location: /tenants/01234567-89ab-cdef-0123-456789abcdef
Content-Type: application/json

{
    "id": "01234567-89ab-cdef-0123-456789abcdef",
    "name": "Acme Corp",
    "slug": "acme",
    "is_active": true,
    "created_at": "2026-03-06T10:30:45.123456Z",
    "updated_at": "2026-03-06T10:30:45.123456Z",
    "created_by": "admin@platform.com",
    "updated_by": "admin@platform.com"
}
```

---

### AC9: Idempotency Handling
**Given** a Platform Admin may accidentally submit duplicate requests  
**When** the same tenant creation request is submitted multiple times  
**Then**:
- First request succeeds with `201 Created`
- Subsequent requests with same slug fail with `409 Conflict`
- No partial state is left (no orphaned schemas or records)

**Verification:**
- Submit same request twice rapidly
- Verify only one tenant is created
- Verify only one schema exists
- Second request returns proper error

---

### AC10: Transaction Integrity
**Given** tenant creation involves multiple operations (DB insert + schema provisioning)  
**When** any operation fails  
**Then**:
- All changes are rolled back (database transaction)
- No tenant record exists in `public.tenants`
- No PostgreSQL schema is created
- Client receives appropriate error message
- System remains in consistent state

**Verification:**
```python
# Simulate schema provisioning failure
def test_rollback_on_schema_failure():
    # Mock schema creation to fail
    with patch('app.db.migrations.provision_tenant_schema', side_effect=Exception("Schema creation failed")):
        with pytest.raises(HTTPException):
            await create_tenant(...)
    
    # Verify no tenant record exists
    tenant = await get_tenant_by_slug("test_slug")
    assert tenant is None
    
    # Verify no schema exists
    schema_exists = await check_schema_exists("tenant_test_slug")
    assert schema_exists == False
```

---

## Technical Implementation Notes

### Current Implementation Status
✅ **Already Implemented:**
- Basic API endpoint at `POST /tenants`
- UUID7 auto-generation in model
- Slug uniqueness validation
- Schema provisioning integration
- Transaction rollback on failure

⚠️ **Needs Enhancement:**
- Platform Admin authorization check
- Comprehensive audit logging
- Rate limiting
- Enhanced slug format validation (Pydantic validator)
- Location header in response
- Idempotency key support

---

### API Endpoint Specification

**Endpoint:** `POST /tenants`

**Request:**
```http
POST /tenants HTTP/1.1
Host: api.platform.example.com
Authorization: Bearer {admin_token}
Content-Type: application/json

{
    "name": "Acme Corporation",
    "slug": "acme"
}
```

**Success Response:**
```http
HTTP/1.1 201 Created
Location: /tenants/01234567-89ab-cdef-0123-456789abcdef
Content-Type: application/json

{
    "id": "01234567-89ab-cdef-0123-456789abcdef",
    "name": "Acme Corporation",
    "slug": "acme",
    "is_active": true,
    "created_at": "2026-03-06T10:30:45.123456Z",
    "updated_at": "2026-03-06T10:30:45.123456Z"
}
```

**Error Responses:**

| Status Code | Scenario | Response Body |
|-------------|----------|---------------|
| 400 Bad Request | Invalid request format | `{"detail": "Invalid JSON"}` |
| 401 Unauthorized | Missing/invalid token | `{"detail": "Not authenticated"}` |
| 403 Forbidden | Not a Platform Admin | `{"detail": "Only Platform Admins can create tenants"}` |
| 409 Conflict | Duplicate slug | `{"detail": "Tenant with slug 'acme' already exists."}` |
| 422 Unprocessable Entity | Validation error | `{"detail": [{"loc": ["body", "slug"], "msg": "..."}]}` |
| 500 Internal Server Error | Server error | `{"detail": "Internal server error"}` |

---

### Service Layer Implementation

```python
# app/services/tenants/tenant.py

async def create_tenant(
    data: TenantCreate,
    session: AsyncSession,
    engine: AsyncEngine,
    database_url: str | None = None,
    actor: str | None = None,  # For audit logging
) -> Tenant:
    """Create a new tenant and provision its PostgreSQL schema.
    
    Steps:
    1. Validate input and check slug uniqueness
    2. Create tenant record in public.tenants
    3. Provision PostgreSQL schema (tenant_{slug})
    4. Run migrations in tenant schema
    5. Log audit event
    6. Return created tenant
    
    Raises:
        HTTPException 409: Slug already exists
        HTTPException 500: Schema provisioning failed
    """
    # Implementation with transaction handling
    # and audit logging...
```

---

## Definition of Done

### Checklist
- [ ] API endpoint `POST /tenants` implemented
- [ ] UUID7 auto-generation working (no client-provided ID)
- [ ] Slug uniqueness validation returns 409 on conflict
- [ ] Request validation enforces required fields and constraints
- [ ] Slug format validation (lowercase, alphanumeric + hyphen/underscore)
- [ ] Default `is_active = true` applied
- [ ] Audit fields auto-populated from user context
- [ ] PostgreSQL schema provisioned automatically
- [ ] Transaction rollback on schema provisioning failure
- [ ] Audit logging for success and failure cases
- [ ] Platform Admin authorization enforced (403 for non-admins)
- [ ] Unit tests for service layer (90%+ coverage)
- [ ] Integration tests for API endpoint
- [ ] Error handling tests (409, 422, 500)
- [ ] Concurrency test (multiple simultaneous creations)
- [ ] API documentation (OpenAPI/Swagger) updated
- [ ] Location header included in 201 response

---

## Test Plan

### Unit Tests

```python
# tests/unit/services/test_tenant_creation.py

async def test_create_tenant_generates_uuid7():
    """Verify UUID7 is auto-generated."""
    tenant = await create_tenant(TenantCreate(name="Test", slug="test"), session, engine)
    assert isinstance(tenant.id, UUID)
    # Verify UUID7 version bits

async def test_create_tenant_slug_uniqueness():
    """Verify duplicate slug returns 409."""
    await create_tenant(TenantCreate(name="Test", slug="test"), session, engine)
    
    with pytest.raises(HTTPException) as exc:
        await create_tenant(TenantCreate(name="Test2", slug="test"), session, engine)
    
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail

async def test_create_tenant_provisions_schema():
    """Verify PostgreSQL schema is created."""
    tenant = await create_tenant(TenantCreate(name="Test", slug="test"), session, engine)
    
    # Check schema exists
    schema_exists = await verify_schema_exists(f"tenant_{tenant.slug}", engine)
    assert schema_exists

async def test_create_tenant_rollback_on_failure():
    """Verify transaction rollback on schema provisioning failure."""
    with patch('app.db.migrations.provision_tenant_schema', side_effect=Exception("Fail")):
        with pytest.raises(Exception):
            await create_tenant(TenantCreate(name="Test", slug="test"), session, engine)
    
    # Verify no tenant record
    tenant = await get_tenant_by_slug("test", session)
    assert tenant is None
```

### Integration Tests

```python
# tests/integration/test_tenant_api.py

async def test_create_tenant_endpoint_success(client, admin_token):
    """Test successful tenant creation via API."""
    response = await client.post(
        "/tenants",
        json={"name": "Acme Corp", "slug": "acme"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    
    assert response.status_code == 201
    assert "Location" in response.headers
    assert response.json()["slug"] == "acme"
    assert response.json()["is_active"] == True

async def test_create_tenant_requires_admin(client, user_token):
    """Test non-admin cannot create tenant."""
    response = await client.post(
        "/tenants",
        json={"name": "Test", "slug": "test"},
        headers={"Authorization": f"Bearer {user_token}"}
    )
    
    assert response.status_code == 403

async def test_create_tenant_validation_errors(client, admin_token):
    """Test validation error responses."""
    # Missing slug
    response = await client.post(
        "/tenants",
        json={"name": "Test"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
    
    # Invalid slug format
    response = await client.post(
        "/tenants",
        json={"name": "Test", "slug": "Invalid Slug!"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
```

---

## Dependencies
- `app/models/public/tenant.py` - Tenant model
- `app/schemas/tenants/tenant.py` - TenantCreate schema
- `app/services/tenants/tenant.py` - Tenant service logic
- `app/db/migrations.py` - Schema provisioning
- `fastapi` - API framework
- `sqlmodel` - ORM
- `uuid_extensions` - UUID7 generation

---

## Future Enhancements
1. Idempotency key support for safe retries
2. Async/background schema provisioning for large schemas
3. Tenant creation webhooks for external systems
4. Bulk tenant creation endpoint
5. Tenant creation wizard with configuration options
6. Tenant template selection during creation

---

## References
- [Subtask 1: Tenant Entity Structure](./subtask-1-tenant-entity-structure.md)
- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
- FastAPI Documentation: https://fastapi.tiangolo.com/
- UUID7 Spec: https://datatracker.ietf.org/doc/html/draft-ietf-uuidrev-rfc4122bis
