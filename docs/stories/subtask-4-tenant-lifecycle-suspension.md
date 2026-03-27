# Subtask 4: Tenant Lifecycle Management and Suspension Enforcement

## Overview
Implement tenant lifecycle state management (Active, Suspended, Inactive) with middleware-level enforcement that blocks API requests from suspended tenants, ensuring comprehensive access control and audit logging for all state transitions.

## User Story
As a Platform Admin
I want to manage tenant lifecycle states and enforce suspensions at the middleware level
So that I can control tenant access to the system while maintaining security and audit compliance

---

## Acceptance Criteria

### AC1: Tenant Status State Machine
**Given** the tenant lifecycle management system  
**When** reviewing tenant status options  
**Then** the following states are supported:

| Status | Description | API Access | Schema Access | Transitions |
|--------|-------------|------------|---------------|-------------|
| `Active` | Normal operation | ✅ Allowed | ✅ Allowed | → Suspended, → Inactive |
| `Suspended` | Temporarily blocked | ❌ Blocked | ❌ Blocked | → Active, → Inactive |
| `Inactive` | Soft-deleted | ❌ Blocked | ❌ Blocked | → Active (admin restore) |

**And** default status on tenant creation is `Active`  
**And** invalid state transitions are rejected (e.g., cannot go directly from Active to arbitrary states without validation)

**Verification:**
```python
# Valid transition
tenant.status = "Active"
await transition_tenant_status(tenant.id, "Suspended")
assert tenant.status == "Suspended"

# Invalid status value
with pytest.raises(ValidationError):
    await transition_tenant_status(tenant.id, "InvalidStatus")
```

---

### AC2: Status Change API Endpoint
**Given** the tenant management system  
**When** a Platform Admin needs to change tenant status  
**Then** an API endpoint is available:

**Endpoint:** `PATCH /tenants/{tenant_id}/status`

**Request:**
```json
{
    "status": "Suspended",
    "reason": "Payment overdue"  // Optional
}
```

**Response:**
```json
{
    "id": "abc-123",
    "name": "Acme Corp",
    "slug": "acme",
    "status": "Suspended",
    "is_active": false,
    "created_at": "2026-03-01T08:00:00.000000Z",
    "updated_at": "2026-03-06T11:00:00.123456Z"
}
```

**And** only Platform Admins can change status  
**And** the reason is logged in audit trail

**Verification:**
```python
PATCH /tenants/abc-123/status
{
    "status": "Suspended",
    "reason": "Payment overdue"
}
Response: 200 OK

# Non-admin attempt
PATCH /tenants/abc-123/status (with user token)
Response: 403 Forbidden
```

---

### AC3: Middleware Suspension Enforcement
**Given** a tenant is in `Suspended` status  
**When** that tenant sends any API request (with valid `X-Tenant-ID` header)  
**Then**:
- The middleware intercepts the request before it reaches route handlers
- The request is blocked and returns `403 Forbidden`
- Response includes clear error: "Tenant is suspended. Please contact support."
- The blocked request is logged for audit purposes

**Verification:**
```python
# Suspend tenant
await transition_tenant_status(tenant_id, "Suspended")

# Attempt API call as suspended tenant
GET /api/templates
Headers: 
  X-Tenant-ID: abc-123
  Authorization: Bearer {tenant_user_token}

Response: 403 Forbidden
{
    "detail": "Tenant is suspended. Please contact support.",
    "error_code": "TENANT_SUSPENDED"
}
```

---

### AC4: Middleware Implementation Details
**Given** the suspension enforcement middleware  
**When** processing requests  
**Then** the middleware:
1. Extracts `X-Tenant-ID` header from request
2. Loads tenant record from database (with caching)
3. Checks tenant status:
   - `Active`: Allow request to proceed
   - `Suspended`: Block with 403 Forbidden
   - `Inactive`: Block with 403 Forbidden
4. For blocked requests: Log event and return error
5. For allowed requests: Attach tenant context and proceed

**And** the middleware:
- Runs on all tenant-scoped endpoints (excludes `/tenants/*` management endpoints)
- Uses caching to minimize database queries (e.g., Redis cache, TTL 60 seconds)
- Handles missing `X-Tenant-ID` gracefully (401 Unauthorized)
- Handles invalid `X-Tenant-ID` gracefully (404 Not Found)

**Verification:**
```python
# Test middleware order
def test_middleware_runs_before_routes():
    # Middleware should block before endpoint logic
    with patch('app.routes.templates.get_templates') as mock:
        response = await client.get(
            "/templates",
            headers={"X-Tenant-ID": suspended_tenant_id}
        )
        assert response.status_code == 403
        assert not mock.called  # Endpoint never reached
```

---

### AC5: Status Transition Validation
**Given** a Platform Admin attempts to change tenant status  
**When** the system validates the transition  
**Then** the following rules are enforced:

| Current Status | Target Status | Allowed? | Notes |
|----------------|---------------|----------|-------|
| Active | Suspended | ✅ Yes | Common operation |
| Active | Inactive | ✅ Yes | Soft delete |
| Suspended | Active | ✅ Yes | Restore access |
| Suspended | Inactive | ✅ Yes | Permanent closure |
| Inactive | Active | ⚠️ Admin-only | Requires explicit restore action |
| Inactive | Suspended | ❌ No | Invalid transition |

**And** invalid transitions return `400 Bad Request` with explanation

**Verification:**
```python
# Valid transition
tenant.status = "Active"
await transition_tenant_status(tenant.id, "Suspended")  # OK

# Invalid transition
tenant.status = "Inactive"
with pytest.raises(HTTPException) as exc:
    await transition_tenant_status(tenant.id, "Suspended")
assert exc.value.status_code == 400
assert "Invalid status transition" in exc.value.detail
```

---

### AC6: Audit Logging for Status Changes
**Given** any tenant status change (successful or failed)  
**When** the operation completes  
**Then** the system logs:

**Success Log:**
```json
{
    "event": "tenant.status_changed",
    "timestamp": "2026-03-06T11:00:00.123Z",
    "actor": "admin@platform.com",
    "tenant_id": "abc-123",
    "tenant_slug": "acme",
    "old_status": "Active",
    "new_status": "Suspended",
    "reason": "Payment overdue",
    "ip_address": "192.168.1.100",
    "user_agent": "Mozilla/5.0...",
    "status": "success"
}
```

**Blocked Request Log:**
```json
{
    "event": "tenant.access_denied",
    "timestamp": "2026-03-06T11:01:30.456Z",
    "tenant_id": "abc-123",
    "tenant_status": "Suspended",
    "requested_endpoint": "/api/templates",
    "method": "GET",
    "user_id": "user@acme.com",
    "ip_address": "203.0.113.42",
    "reason": "Tenant suspended",
    "status": "blocked"
}
```

**Verification:**
- Logs include status transition details
- Logs include reason for status change
- Blocked access attempts are logged
- Logs are searchable and queryable

---

### AC7: Tenant Reactivation
**Given** a tenant is in `Suspended` or `Inactive` status  
**When** a Platform Admin reactivates the tenant  
**Then**:
- Status changes to `Active`
- `is_active` flag is set to `true`
- Middleware immediately allows requests (cache invalidation)
- Reactivation is logged in audit trail
- All tenant users regain access

**Verification:**
```python
# Suspend tenant
await transition_tenant_status(tenant_id, "Suspended")

# Verify access blocked
response = await client.get("/templates", headers={"X-Tenant-ID": tenant_id})
assert response.status_code == 403

# Reactivate tenant
await transition_tenant_status(tenant_id, "Active")

# Verify access restored
response = await client.get("/templates", headers={"X-Tenant-ID": tenant_id})
assert response.status_code == 200
```

---

### AC8: Bulk Status Operations (Optional)
**Given** Platform Admin needs to suspend/activate multiple tenants  
**When** using bulk operations endpoint  
**Then**:
- Endpoint: `PATCH /tenants/bulk/status`
- Request body contains list of tenant IDs and target status
- Each operation is validated independently
- Partial success is supported (some succeed, some fail)
- Response includes success/failure for each tenant

**Note:** This is optional and can be deferred to future enhancement.

---

### AC9: Integration with `is_active` Flag
**Given** the existing `is_active` boolean flag  
**When** implementing status enum  
**Then**:
- `is_active = true` corresponds to `status = "Active"`
- `is_active = false` corresponds to `status = "Suspended"` or `"Inactive"`
- Keep both fields for backward compatibility during migration
- Eventually deprecate `is_active` in favor of `status`

**Verification:**
```python
tenant.status = "Active"
assert tenant.is_active == True

tenant.status = "Suspended"
assert tenant.is_active == False

tenant.status = "Inactive"
assert tenant.is_active == False
```

---

### AC10: Performance and Caching
**Given** the suspension middleware runs on every tenant request  
**When** optimizing for performance  
**Then**:
- Tenant status is cached in Redis or in-memory cache
- Cache TTL is 60 seconds (configurable)
- Cache is invalidated on status change
- Database query fallback if cache miss
- Middleware latency is < 5ms for cached lookups

**Verification:**
```python
# First request - cache miss
start = time.time()
response = await client.get("/templates", headers={"X-Tenant-ID": tenant_id})
first_duration = time.time() - start

# Second request - cache hit
start = time.time()
response = await client.get("/templates", headers={"X-Tenant-ID": tenant_id})
second_duration = time.time() - start

assert second_duration < first_duration
assert second_duration < 0.005  # < 5ms
```

---

### AC11: Status Change Notifications (Future)
**Given** a tenant status changes  
**When** the change is persisted  
**Then** (future enhancement):
- Send email notification to tenant admin contacts
- Post webhook to configured tenant callback URL
- Emit event to message queue for downstream systems

**Note:** This is a future enhancement and not required for initial implementation.

---

### AC12: Platform Admin Endpoints Exemption
**Given** the suspension middleware is active  
**When** Platform Admin accesses tenant management endpoints  
**Then**:
- `/tenants/*` endpoints are exempt from tenant suspension checks
- Platform Admin can manage suspended tenants
- Platform Admin can view suspended tenant details

**Verification:**
```python
# Suspend tenant
await transition_tenant_status(tenant_id, "Suspended")

# Platform Admin can still access tenant management
GET /tenants/abc-123 (with admin token)
Response: 200 OK

PATCH /tenants/abc-123/status (with admin token)
Response: 200 OK

# But tenant users are blocked from their own APIs
GET /templates (with tenant user token, X-Tenant-ID: abc-123)
Response: 403 Forbidden
```

---

## Technical Implementation Notes

### Current Implementation Status
⚠️ **Needs Implementation:**
- Status enum (currently only `is_active` boolean exists)
- Status transition validation
- Suspension enforcement middleware
- Status change API endpoint
- Audit logging for status changes
- Cache integration for performance
- Status transition state machine

---

### Status Enum Definition

```python
# app/models/public/tenant.py

from enum import Enum

class TenantStatus(str, Enum):
    """Tenant lifecycle status."""
    ACTIVE = "Active"
    SUSPENDED = "Suspended"
    INACTIVE = "Inactive"

class Tenant(PublicSchemaModel, table=True):
    # ... existing fields ...
    
    status: TenantStatus = Field(
        default=TenantStatus.ACTIVE,
        sa_type=sa.Enum(TenantStatus),
        nullable=False,
    )
    
    # Keep for backward compatibility
    is_active: bool = Field(default=True)
    
    @property
    def is_suspended(self) -> bool:
        """Check if tenant is suspended."""
        return self.status == TenantStatus.SUSPENDED
    
    @property
    def is_accessible(self) -> bool:
        """Check if tenant can access APIs."""
        return self.status == TenantStatus.ACTIVE
```

---

### Middleware Implementation

```python
# app/middleware/tenant_suspension.py

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.tenants import get_tenant_by_id
from app.core.cache import cache_get, cache_set

class TenantSuspensionMiddleware(BaseHTTPMiddleware):
    """Enforce tenant suspension at middleware level."""
    
    async def dispatch(self, request: Request, call_next):
        # Skip for non-tenant endpoints (platform admin routes)
        if request.url.path.startswith("/tenants"):
            return await call_next(request)
        
        # Extract tenant ID from header
        tenant_id = request.headers.get("X-Tenant-ID")
        if not tenant_id:
            # No tenant context, let endpoint handle
            return await call_next(request)
        
        # Check tenant status (with caching)
        tenant_status = await self._get_tenant_status(tenant_id)
        
        if tenant_status != "Active":
            # Log blocked access
            await log_audit_event(
                event="tenant.access_denied",
                tenant_id=tenant_id,
                tenant_status=tenant_status,
                requested_endpoint=request.url.path,
                method=request.method,
            )
            
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant is suspended. Please contact support.",
            )
        
        # Attach tenant to request state
        request.state.tenant_id = tenant_id
        request.state.tenant_status = tenant_status
        
        return await call_next(request)
    
    async def _get_tenant_status(self, tenant_id: str) -> str:
        """Get tenant status with caching."""
        cache_key = f"tenant_status:{tenant_id}"
        
        # Check cache
        cached_status = await cache_get(cache_key)
        if cached_status:
            return cached_status
        
        # Fetch from database
        tenant = await get_tenant_by_id(tenant_id)
        if not tenant:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        
        # Cache for 60 seconds
        await cache_set(cache_key, tenant.status, ttl=60)
        
        return tenant.status
```

---

### Status Change Service

```python
# app/services/tenants/tenant.py

async def transition_tenant_status(
    tenant_id: UUID,
    new_status: TenantStatus,
    reason: str | None = None,
    actor: str | None = None,
    session: AsyncSession = None,
) -> Tenant:
    """Change tenant status with validation and audit logging.
    
    Raises:
        HTTPException 400: Invalid status transition
        HTTPException 404: Tenant not found
    """
    tenant = await get_tenant(tenant_id, session)
    old_status = tenant.status
    
    # Validate transition
    if not is_valid_transition(old_status, new_status):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status transition from {old_status} to {new_status}",
        )
    
    # Update status
    tenant.status = new_status
    tenant.is_active = (new_status == TenantStatus.ACTIVE)
    
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    
    # Invalidate cache
    await cache_delete(f"tenant_status:{tenant_id}")
    
    # Log transition
    await log_audit_event(
        event="tenant.status_changed",
        actor=actor,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
    )
    
    return tenant

def is_valid_transition(old: TenantStatus, new: TenantStatus) -> bool:
    """Validate status transition."""
    valid_transitions = {
        TenantStatus.ACTIVE: {TenantStatus.SUSPENDED, TenantStatus.INACTIVE},
        TenantStatus.SUSPENDED: {TenantStatus.ACTIVE, TenantStatus.INACTIVE},
        TenantStatus.INACTIVE: {TenantStatus.ACTIVE},  # Restore only
    }
    return new in valid_transitions.get(old, set())
```

---

### API Route

```python
# app/routes/tenants/tenant.py

@router.patch("/{tenant_id}/status", response_model=TenantRead)
async def change_tenant_status(
    tenant_id: UUID,
    data: TenantStatusChange,
    session: AsyncSession = Depends(get_public_session),
    current_user: User = Depends(require_platform_admin),
):
    """Change tenant lifecycle status (Platform Admin only)."""
    return await tenant_svc.transition_tenant_status(
        tenant_id=tenant_id,
        new_status=data.status,
        reason=data.reason,
        actor=current_user.email,
        session=session,
    )
```

---

### Schema

```python
# app/schemas/tenants/tenant.py

class TenantStatusChange(SQLModel):
    """Request body for status change."""
    status: TenantStatus
    reason: str | None = Field(default=None, max_length=500)
```

---

## Definition of Done

### Checklist
- [ ] TenantStatus enum defined (Active, Suspended, Inactive)
- [ ] Database migration adds `status` column
- [ ] Status transition validation implemented
- [ ] Status change API endpoint created (`PATCH /tenants/{id}/status`)
- [ ] Suspension enforcement middleware implemented
- [ ] Middleware runs on all tenant-scoped routes
- [ ] Middleware exempts Platform Admin routes (`/tenants/*`)
- [ ] Cache integration for tenant status lookups
- [ ] Cache invalidation on status changes
- [ ] Audit logging for status changes
- [ ] Audit logging for blocked access attempts
- [ ] Platform Admin authorization enforced
- [ ] Integration with `is_active` flag maintained
- [ ] Unit tests for status transitions (90%+ coverage)
- [ ] Unit tests for middleware enforcement
- [ ] Integration tests for API endpoint
- [ ] Performance tests for middleware latency
- [ ] API documentation (OpenAPI/Swagger) updated

---

## Test Plan

### Unit Tests

```python
# tests/unit/services/test_tenant_status.py

async def test_valid_status_transition():
    """Test valid status transition."""
    tenant = await create_tenant(...)
    assert tenant.status == TenantStatus.ACTIVE
    
    updated = await transition_tenant_status(tenant.id, TenantStatus.SUSPENDED)
    assert updated.status == TenantStatus.SUSPENDED
    assert updated.is_active == False

async def test_invalid_status_transition():
    """Test invalid status transition is rejected."""
    tenant = await create_tenant(...)
    await transition_tenant_status(tenant.id, TenantStatus.INACTIVE)
    
    with pytest.raises(HTTPException) as exc:
        await transition_tenant_status(tenant.id, TenantStatus.SUSPENDED)
    
    assert exc.value.status_code == 400
    assert "Invalid status transition" in exc.value.detail

async def test_status_cache_invalidation():
    """Test cache is invalidated on status change."""
    tenant = await create_tenant(...)
    
    # Populate cache
    await get_tenant_status_cached(tenant.id)
    assert await cache_get(f"tenant_status:{tenant.id}") == "Active"
    
    # Change status
    await transition_tenant_status(tenant.id, TenantStatus.SUSPENDED)
    
    # Cache should be invalidated
    assert await cache_get(f"tenant_status:{tenant.id}") is None
```

### Middleware Tests

```python
# tests/unit/middleware/test_tenant_suspension.py

async def test_middleware_blocks_suspended_tenant():
    """Test middleware blocks requests from suspended tenants."""
    tenant = await create_tenant(...)
    await transition_tenant_status(tenant.id, TenantStatus.SUSPENDED)
    
    response = await client.get(
        "/templates",
        headers={"X-Tenant-ID": str(tenant.id)}
    )
    
    assert response.status_code == 403
    assert "suspended" in response.json()["detail"].lower()

async def test_middleware_allows_active_tenant():
    """Test middleware allows requests from active tenants."""
    tenant = await create_tenant(...)
    
    response = await client.get(
        "/templates",
        headers={"X-Tenant-ID": str(tenant.id)}
    )
    
    assert response.status_code == 200

async def test_middleware_exempts_admin_routes():
    """Test middleware does not block Platform Admin routes."""
    tenant = await create_tenant(...)
    await transition_tenant_status(tenant.id, TenantStatus.SUSPENDED)
    
    # Admin can still access tenant management
    response = await client.get(
        f"/tenants/{tenant.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    
    assert response.status_code == 200
```

### Integration Tests

```python
# tests/integration/test_tenant_lifecycle.py

async def test_end_to_end_suspension_enforcement():
    """Test full suspension workflow."""
    # 1. Create active tenant
    tenant = await create_tenant_via_api(...)
    assert tenant["status"] == "Active"
    
    # 2. Verify tenant can access APIs
    response = await client.get(
        "/templates",
        headers={"X-Tenant-ID": tenant["id"]}
    )
    assert response.status_code == 200
    
    # 3. Suspend tenant
    response = await client.patch(
        f"/tenants/{tenant['id']}/status",
        json={"status": "Suspended", "reason": "Payment overdue"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    
    # 4. Verify tenant is blocked
    response = await client.get(
        "/templates",
        headers={"X-Tenant-ID": tenant["id"]}
    )
    assert response.status_code == 403
    
    # 5. Reactivate tenant
    response = await client.patch(
        f"/tenants/{tenant['id']}/status",
        json={"status": "Active"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    
    # 6. Verify tenant can access APIs again
    response = await client.get(
        "/templates",
        headers={"X-Tenant-ID": tenant["id"]}
    )
    assert response.status_code == 200
```

---

## Dependencies
- `app/models/public/tenant.py` - Tenant model with status enum
- `app/middleware/tenant_suspension.py` - Suspension enforcement middleware
- `app/services/tenants/tenant.py` - Status transition logic
- `app/core/cache.py` - Redis/in-memory cache
- `fastapi` - API framework
- `sqlmodel` - ORM

---

## Future Enhancements
1. Scheduled suspension (e.g., suspend on specific date/time)
2. Auto-reactivation after payment received
3. Status change webhooks and notifications
4. Bulk status operations
5. Status history tracking (audit table)
6. Grace period before hard suspension
7. Custom suspension messages per tenant
8. Status-based feature flags

---

## References
- [Subtask 1: Tenant Entity Structure](./subtask-1-tenant-entity-structure.md)
- [Subtask 2: Tenant Creation API](./subtask-2-tenant-creation-api.md)
- [Subtask 3: Tenant Update API](./subtask-3-tenant-update-api.md)
- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
