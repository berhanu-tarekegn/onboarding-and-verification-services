# Subtask 5: Tenant Retrieval APIs with Pagination and Filtering

## Overview
Develop comprehensive APIs for retrieving tenant information by ID and listing tenants with pagination, filtering, and sorting capabilities to support Platform Admin tenant management workflows.

## User Story
As a Platform Admin
I want to retrieve individual tenants and list all tenants with filtering options
So that I can efficiently manage and monitor the tenant registry

---

## Acceptance Criteria

### AC1: Get Tenant by ID Endpoint
**Given** a Platform Admin needs to view a specific tenant  
**When** they request tenant details by ID  
**Then**:
- Endpoint: `GET /tenants/{tenant_id}`
- Returns complete tenant record with all fields
- Returns `200 OK` on success
- Returns `404 Not Found` if tenant doesn't exist
- Only Platform Admins can access this endpoint

**Request Example:**
```http
GET /tenants/abc-123 HTTP/1.1
Host: api.platform.example.com
Authorization: Bearer {admin_token}
```

**Success Response:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
    "id": "abc-123",
    "name": "Acme Corporation",
    "slug": "acme",
    "status": "Active",
    "is_active": true,
    "created_at": "2026-03-01T08:00:00.000000Z",
    "updated_at": "2026-03-06T10:00:00.000000Z",
    "created_by": "admin@platform.com",
    "updated_by": "admin@platform.com"
}
```

**Error Response (Not Found):**
```json
HTTP/1.1 404 Not Found
Content-Type: application/json

{
    "detail": "Tenant not found."
}
```

**Verification:**
```python
# Existing tenant
response = await client.get(
    "/tenants/existing-id",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.status_code == 200

# Non-existent tenant
response = await client.get(
    "/tenants/non-existent-id",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.status_code == 404
```

---

### AC2: List Tenants with Pagination
**Given** a Platform Admin needs to view all tenants  
**When** they request the tenant list  
**Then**:
- Endpoint: `GET /tenants`
- Returns paginated list of tenants
- Default page size: 20 tenants
- Maximum page size: 100 tenants
- Includes pagination metadata in response

**Request Example:**
```http
GET /tenants?page=1&page_size=20 HTTP/1.1
Host: api.platform.example.com
Authorization: Bearer {admin_token}
```

**Response Format:**
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
    "items": [
        {
            "id": "abc-123",
            "name": "Acme Corporation",
            "slug": "acme",
            "status": "Active",
            "is_active": true,
            "created_at": "2026-03-01T08:00:00.000000Z",
            "updated_at": "2026-03-06T10:00:00.000000Z"
        },
        {
            "id": "def-456",
            "name": "Beta Industries",
            "slug": "beta",
            "status": "Suspended",
            "is_active": false,
            "created_at": "2026-03-02T09:00:00.000000Z",
            "updated_at": "2026-03-05T15:30:00.000000Z"
        }
    ],
    "total": 45,
    "page": 1,
    "page_size": 20,
    "total_pages": 3
}
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | Integer | 1 | Page number (1-indexed) |
| `page_size` | Integer | 20 | Items per page (max 100) |

**Verification:**
```python
# Default pagination
response = await client.get("/tenants", headers={"Authorization": f"Bearer {admin_token}"})
assert response.status_code == 200
assert response.json()["page"] == 1
assert response.json()["page_size"] == 20
assert len(response.json()["items"]) <= 20

# Custom page size
response = await client.get(
    "/tenants?page=2&page_size=10",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.json()["page"] == 2
assert response.json()["page_size"] == 10
```

---

### AC3: Filter by Status
**Given** a Platform Admin wants to view tenants by status  
**When** they apply status filter  
**Then**:
- Query parameter: `status` (Active, Suspended, Inactive)
- Returns only tenants matching the status
- Can combine with pagination

**Request Example:**
```http
GET /tenants?status=Suspended&page=1&page_size=20 HTTP/1.1
Authorization: Bearer {admin_token}
```

**Response:**
```json
{
    "items": [
        {
            "id": "def-456",
            "name": "Beta Industries",
            "slug": "beta",
            "status": "Suspended",
            "is_active": false,
            ...
        }
    ],
    "total": 5,
    "page": 1,
    "page_size": 20,
    "total_pages": 1
}
```

**Verification:**
```python
# Filter active tenants
response = await client.get(
    "/tenants?status=Active",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert all(t["status"] == "Active" for t in response.json()["items"])

# Filter suspended tenants
response = await client.get(
    "/tenants?status=Suspended",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert all(t["status"] == "Suspended" for t in response.json()["items"])
```

---

### AC4: Search by Name or Slug
**Given** a Platform Admin wants to find specific tenants  
**When** they use search functionality  
**Then**:
- Query parameter: `search` (searches name and slug)
- Case-insensitive partial match
- Can combine with status filter and pagination

**Request Example:**
```http
GET /tenants?search=acme&page=1&page_size=20 HTTP/1.1
Authorization: Bearer {admin_token}
```

**Response:**
```json
{
    "items": [
        {
            "id": "abc-123",
            "name": "Acme Corporation",
            "slug": "acme",
            ...
        },
        {
            "id": "xyz-789",
            "name": "Acme Industries",
            "slug": "acme-industries",
            ...
        }
    ],
    "total": 2,
    ...
}
```

**Verification:**
```python
# Search by name
response = await client.get(
    "/tenants?search=acme",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert all("acme" in t["name"].lower() or "acme" in t["slug"].lower() 
           for t in response.json()["items"])

# Case-insensitive search
response = await client.get(
    "/tenants?search=ACME",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.status_code == 200
assert response.json()["total"] > 0
```

---

### AC5: Sorting Options
**Given** a Platform Admin wants to sort tenant list  
**When** they specify sort parameters  
**Then**:
- Query parameter: `sort_by` (name, created_at, updated_at, status)
- Query parameter: `sort_order` (asc, desc)
- Default sort: `name` ascending

**Request Example:**
```http
GET /tenants?sort_by=created_at&sort_order=desc HTTP/1.1
Authorization: Bearer {admin_token}
```

**Response:**
```json
{
    "items": [
        {
            "id": "newest-id",
            "name": "Newest Tenant",
            "created_at": "2026-03-06T12:00:00.000000Z",
            ...
        },
        {
            "id": "older-id",
            "name": "Older Tenant",
            "created_at": "2026-03-05T10:00:00.000000Z",
            ...
        }
    ],
    ...
}
```

**Supported Sort Fields:**

| Field | Description |
|-------|-------------|
| `name` | Tenant name (default) |
| `created_at` | Creation timestamp |
| `updated_at` | Last update timestamp |
| `status` | Lifecycle status |
| `slug` | Tenant slug |

**Verification:**
```python
# Sort by creation date (newest first)
response = await client.get(
    "/tenants?sort_by=created_at&sort_order=desc",
    headers={"Authorization": f"Bearer {admin_token}"}
)
items = response.json()["items"]
assert items[0]["created_at"] >= items[-1]["created_at"]

# Sort by name (ascending)
response = await client.get(
    "/tenants?sort_by=name&sort_order=asc",
    headers={"Authorization": f"Bearer {admin_token}"}
)
items = response.json()["items"]
names = [t["name"] for t in items]
assert names == sorted(names)
```

---

### AC6: Combined Filtering
**Given** a Platform Admin needs complex queries  
**When** they combine multiple filters  
**Then** all filters are applied together (AND logic)

**Request Example:**
```http
GET /tenants?status=Active&search=acme&sort_by=created_at&sort_order=desc&page=1&page_size=10
Authorization: Bearer {admin_token}
```

**Verification:**
```python
response = await client.get(
    "/tenants?status=Active&search=acme&sort_by=created_at&sort_order=desc",
    headers={"Authorization": f"Bearer {admin_token}"}
)

items = response.json()["items"]
# All items are Active
assert all(t["status"] == "Active" for t in items)
# All items match search term
assert all("acme" in t["name"].lower() or "acme" in t["slug"].lower() for t in items)
# Items are sorted by created_at descending
if len(items) > 1:
    assert items[0]["created_at"] >= items[-1]["created_at"]
```

---

### AC7: Platform Admin Authorization
**Given** the tenant retrieval endpoints exist  
**When** a request is received  
**Then**:
- Only users with `PlatformAdmin` role can access
- Non-admin users receive `403 Forbidden`
- Unauthenticated requests receive `401 Unauthorized`

**Verification:**
```python
# Unauthenticated
GET /tenants (no auth header)
Response: 401 Unauthorized

# Regular user (not admin)
GET /tenants (with user token)
Response: 403 Forbidden

# Platform Admin
GET /tenants (with admin token)
Response: 200 OK
```

---

### AC8: Empty Result Handling
**Given** a tenant list query returns no results  
**When** no tenants match the criteria  
**Then**:
- Returns `200 OK` (not 404)
- Response contains empty `items` array
- `total` is 0
- Pagination metadata is still present

**Response Example:**
```json
HTTP/1.1 200 OK

{
    "items": [],
    "total": 0,
    "page": 1,
    "page_size": 20,
    "total_pages": 0
}
```

**Verification:**
```python
# Search for non-existent tenant
response = await client.get(
    "/tenants?search=nonexistent123xyz",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.status_code == 200
assert response.json()["items"] == []
assert response.json()["total"] == 0
```

---

### AC9: Performance Requirements
**Given** the tenant list endpoint  
**When** handling large datasets  
**Then**:
- Query executes in < 100ms for database with < 10,000 tenants
- Pagination uses efficient LIMIT/OFFSET or cursor-based approach
- Database indexes are used for sorting and filtering
- No N+1 query problems

**Verification:**
```python
import time

start = time.time()
response = await client.get(
    "/tenants?page=1&page_size=20",
    headers={"Authorization": f"Bearer {admin_token}"}
)
duration = time.time() - start

assert response.status_code == 200
assert duration < 0.1  # < 100ms
```

---

### AC10: Validation of Query Parameters
**Given** invalid query parameters are provided  
**When** the API validates the request  
**Then**:
- `page` must be positive integer (>= 1)
- `page_size` must be 1-100
- `sort_by` must be valid field name
- `sort_order` must be "asc" or "desc"
- `status` must be valid TenantStatus value
- Invalid values return `422 Unprocessable Entity`

**Verification:**
```python
# Invalid page (0)
response = await client.get("/tenants?page=0", headers={"Authorization": f"Bearer {admin_token}"})
assert response.status_code == 422

# Invalid page_size (101)
response = await client.get("/tenants?page_size=101", headers={"Authorization": f"Bearer {admin_token}"})
assert response.status_code == 422

# Invalid sort_by
response = await client.get("/tenants?sort_by=invalid_field", headers={"Authorization": f"Bearer {admin_token}"})
assert response.status_code == 422

# Invalid status
response = await client.get("/tenants?status=InvalidStatus", headers={"Authorization": f"Bearer {admin_token}"})
assert response.status_code == 422
```

---

### AC11: Get Tenant by Slug (Additional Endpoint)
**Given** Platform Admin may want to lookup by slug  
**When** they know the tenant slug  
**Then**:
- Endpoint: `GET /tenants/by-slug/{slug}`
- Returns complete tenant record
- Returns `404 Not Found` if slug doesn't exist

**Request Example:**
```http
GET /tenants/by-slug/acme HTTP/1.1
Authorization: Bearer {admin_token}
```

**Response:**
```json
HTTP/1.1 200 OK

{
    "id": "abc-123",
    "name": "Acme Corporation",
    "slug": "acme",
    ...
}
```

**Verification:**
```python
response = await client.get(
    "/tenants/by-slug/acme",
    headers={"Authorization": f"Bearer {admin_token}"}
)
assert response.status_code == 200
assert response.json()["slug"] == "acme"
```

---

### AC12: Response Field Consistency
**Given** all tenant retrieval endpoints  
**When** they return tenant data  
**Then**:
- All endpoints return the same field structure (TenantRead schema)
- Timestamps are in ISO 8601 format with timezone
- UUIDs are in standard string format
- Field names use snake_case

**Verification:**
```python
# Get by ID
get_response = await client.get(f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {admin_token}"})
get_tenant = get_response.json()

# List
list_response = await client.get("/tenants", headers={"Authorization": f"Bearer {admin_token}"})
list_tenant = list_response.json()["items"][0]

# Same field structure
assert set(get_tenant.keys()) == set(list_tenant.keys())
```

---

## Technical Implementation Notes

### Current Implementation Status
✅ **Already Implemented:**
- Basic `GET /tenants` endpoint (list all)
- Basic `GET /tenants/{tenant_id}` endpoint (get by ID)
- `get_tenant` service function
- `list_tenants` service function

⚠️ **Needs Enhancement:**
- Pagination support
- Status filtering
- Search functionality
- Sorting options
- Query parameter validation
- Platform Admin authorization
- Performance optimization (indexes)
- `GET /tenants/by-slug/{slug}` endpoint

---

### API Endpoint Specifications

#### 1. Get Tenant by ID
```python
@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_public_session),
    current_user: User = Depends(require_platform_admin),
):
    """Get a tenant by ID (Platform Admin only)."""
    return await tenant_svc.get_tenant(tenant_id, session)
```

#### 2. List Tenants with Pagination
```python
@router.get("", response_model=TenantListResponse)
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: TenantStatus | None = None,
    search: str | None = None,
    sort_by: str = Query("name", regex="^(name|created_at|updated_at|status|slug)$"),
    sort_order: str = Query("asc", regex="^(asc|desc)$"),
    session: AsyncSession = Depends(get_public_session),
    current_user: User = Depends(require_platform_admin),
):
    """List tenants with pagination and filtering (Platform Admin only)."""
    return await tenant_svc.list_tenants_paginated(
        session=session,
        page=page,
        page_size=page_size,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
```

#### 3. Get Tenant by Slug
```python
@router.get("/by-slug/{slug}", response_model=TenantRead)
async def get_tenant_by_slug(
    slug: str,
    session: AsyncSession = Depends(get_public_session),
    current_user: User = Depends(require_platform_admin),
):
    """Get a tenant by slug (Platform Admin only)."""
    tenant = await tenant_svc.get_tenant_by_slug(slug, session)
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    return tenant
```

---

### Service Layer Implementation

```python
# app/services/tenants/tenant.py

from typing import Optional
from sqlmodel import select, func, or_

async def list_tenants_paginated(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status: Optional[TenantStatus] = None,
    search: Optional[str] = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> dict:
    """List tenants with pagination and filtering.
    
    Returns:
        {
            "items": List[Tenant],
            "total": int,
            "page": int,
            "page_size": int,
            "total_pages": int
        }
    """
    # Build base query
    query = select(Tenant)
    
    # Apply status filter
    if status:
        query = query.where(Tenant.status == status)
    
    # Apply search filter (name or slug)
    if search:
        search_pattern = f"%{search.lower()}%"
        query = query.where(
            or_(
                func.lower(Tenant.name).like(search_pattern),
                func.lower(Tenant.slug).like(search_pattern),
            )
        )
    
    # Get total count (before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()
    
    # Apply sorting
    sort_field = getattr(Tenant, sort_by)
    if sort_order == "desc":
        query = query.order_by(sort_field.desc())
    else:
        query = query.order_by(sort_field.asc())
    
    # Apply pagination
    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)
    
    # Execute query
    result = await session.execute(query)
    items = list(result.scalars().all())
    
    # Calculate total pages
    total_pages = (total + page_size - 1) // page_size  # Ceiling division
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
```

---

### Response Schema

```python
# app/schemas/tenants/tenant.py

class TenantListResponse(SQLModel):
    """Response for paginated tenant list."""
    items: list[TenantRead]
    total: int
    page: int
    page_size: int
    total_pages: int
```

---

### Database Indexes

```sql
-- Ensure indexes exist for performance
CREATE INDEX IF NOT EXISTS idx_tenants_name ON public.tenants(name);
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON public.tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON public.tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_created_at ON public.tenants(created_at);
CREATE INDEX IF NOT EXISTS idx_tenants_updated_at ON public.tenants(updated_at);

-- Composite index for search
CREATE INDEX IF NOT EXISTS idx_tenants_name_slug ON public.tenants(name, slug);
```

---

## Definition of Done

### Checklist
- [ ] `GET /tenants/{tenant_id}` endpoint with Platform Admin auth
- [ ] `GET /tenants` endpoint with pagination support
- [ ] Query parameters: page, page_size validated
- [ ] Status filter implemented
- [ ] Search filter implemented (name and slug)
- [ ] Sorting implemented (sort_by, sort_order)
- [ ] Combined filtering works correctly
- [ ] `GET /tenants/by-slug/{slug}` endpoint implemented
- [ ] Platform Admin authorization enforced
- [ ] Empty result handling (200 with empty array)
- [ ] Query parameter validation (422 for invalid values)
- [ ] Database indexes created for performance
- [ ] Pagination metadata in response
- [ ] Unit tests for service layer (90%+ coverage)
- [ ] Integration tests for all endpoints
- [ ] Performance tests (query execution < 100ms)
- [ ] API documentation (OpenAPI/Swagger) updated

---

## Test Plan

### Unit Tests

```python
# tests/unit/services/test_tenant_retrieval.py

async def test_get_tenant_by_id():
    """Test retrieving tenant by ID."""
    tenant = await create_tenant(...)
    retrieved = await get_tenant(tenant.id, session)
    assert retrieved.id == tenant.id
    assert retrieved.name == tenant.name

async def test_get_tenant_not_found():
    """Test 404 for non-existent tenant."""
    with pytest.raises(HTTPException) as exc:
        await get_tenant(uuid4(), session)
    assert exc.value.status_code == 404

async def test_list_tenants_pagination():
    """Test pagination works correctly."""
    # Create 25 tenants
    for i in range(25):
        await create_tenant(TenantCreate(name=f"Tenant {i}", slug=f"tenant-{i}"), session)
    
    # Get page 1 (20 items)
    result = await list_tenants_paginated(session, page=1, page_size=20)
    assert len(result["items"]) == 20
    assert result["total"] == 25
    assert result["total_pages"] == 2
    
    # Get page 2 (5 items)
    result = await list_tenants_paginated(session, page=2, page_size=20)
    assert len(result["items"]) == 5

async def test_list_tenants_status_filter():
    """Test status filtering."""
    active_tenant = await create_tenant(...)
    suspended_tenant = await create_tenant(...)
    await transition_tenant_status(suspended_tenant.id, TenantStatus.SUSPENDED)
    
    # Filter by Active
    result = await list_tenants_paginated(session, status=TenantStatus.ACTIVE)
    assert all(t.status == TenantStatus.ACTIVE for t in result["items"])
    
    # Filter by Suspended
    result = await list_tenants_paginated(session, status=TenantStatus.SUSPENDED)
    assert all(t.status == TenantStatus.SUSPENDED for t in result["items"])

async def test_list_tenants_search():
    """Test search functionality."""
    await create_tenant(TenantCreate(name="Acme Corp", slug="acme"), session)
    await create_tenant(TenantCreate(name="Beta Inc", slug="beta"), session)
    
    # Search by name
    result = await list_tenants_paginated(session, search="acme")
    assert len(result["items"]) == 1
    assert result["items"][0].name == "Acme Corp"
    
    # Case-insensitive search
    result = await list_tenants_paginated(session, search="ACME")
    assert len(result["items"]) == 1

async def test_list_tenants_sorting():
    """Test sorting functionality."""
    # Create tenants with different timestamps
    tenant1 = await create_tenant(TenantCreate(name="Zebra", slug="zebra"), session)
    await asyncio.sleep(0.1)
    tenant2 = await create_tenant(TenantCreate(name="Alpha", slug="alpha"), session)
    
    # Sort by name ascending
    result = await list_tenants_paginated(session, sort_by="name", sort_order="asc")
    assert result["items"][0].name == "Alpha"
    
    # Sort by created_at descending
    result = await list_tenants_paginated(session, sort_by="created_at", sort_order="desc")
    assert result["items"][0].id == tenant2.id
```

### Integration Tests

```python
# tests/integration/test_tenant_retrieval_api.py

async def test_get_tenant_endpoint(client, admin_token):
    """Test GET /tenants/{id} endpoint."""
    # Create tenant
    create_response = await client.post(
        "/tenants",
        json={"name": "Test", "slug": "test"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    tenant_id = create_response.json()["id"]
    
    # Get tenant
    response = await client.get(
        f"/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == tenant_id

async def test_list_tenants_endpoint(client, admin_token):
    """Test GET /tenants endpoint with pagination."""
    # Create multiple tenants
    for i in range(5):
        await client.post(
            "/tenants",
            json={"name": f"Tenant {i}", "slug": f"tenant-{i}"},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
    
    # List tenants
    response = await client.get(
        "/tenants?page=1&page_size=3",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3
    assert response.json()["total"] >= 5

async def test_list_tenants_filters(client, admin_token):
    """Test combined filters."""
    response = await client.get(
        "/tenants?status=Active&search=test&sort_by=created_at&sort_order=desc",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    
    items = response.json()["items"]
    # Verify all are Active
    assert all(t["status"] == "Active" for t in items)

async def test_get_tenant_requires_admin(client, user_token):
    """Test non-admin cannot access endpoints."""
    response = await client.get(
        "/tenants",
        headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 403

async def test_query_parameter_validation(client, admin_token):
    """Test invalid query parameters are rejected."""
    # Invalid page
    response = await client.get(
        "/tenants?page=0",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
    
    # Invalid page_size
    response = await client.get(
        "/tenants?page_size=101",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
```

---

## Dependencies
- `app/models/public/tenant.py` - Tenant model
- `app/schemas/tenants/tenant.py` - TenantRead, TenantListResponse schemas
- `app/services/tenants/tenant.py` - Tenant retrieval logic
- `fastapi` - API framework
- `sqlmodel` - ORM

---

## Future Enhancements
1. Cursor-based pagination for better performance with large datasets
2. Export tenant list to CSV/Excel
3. Advanced filtering (date ranges, multiple statuses)
4. Full-text search using PostgreSQL tsvector
5. Tenant statistics in list response (user count, submission count, etc.)
6. Saved filters/views for Platform Admins
7. Bulk operations on filtered results
8. GraphQL endpoint for flexible querying

---

## References
- [Subtask 1: Tenant Entity Structure](./subtask-1-tenant-entity-structure.md)
- [Subtask 2: Tenant Creation API](./subtask-2-tenant-creation-api.md)
- [Subtask 3: Tenant Update API](./subtask-3-tenant-update-api.md)
- [Subtask 4: Tenant Lifecycle Management](./subtask-4-tenant-lifecycle-suspension.md)
- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
