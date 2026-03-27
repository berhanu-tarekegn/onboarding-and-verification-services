# Subtask 1: Define Tenant Entity Structure

## Overview
Define the Tenant entity structure with immutable TenantID, lifecycle status management, and comprehensive audit tracking to support secure multi-tenant isolation.

## User Story
As a Platform Admin
I want a well-defined Tenant entity
So that the system can reliably manage tenant metadata with proper immutability, status tracking, and audit capabilities

---

## Acceptance Criteria

### AC1: Immutable Tenant Identifier
**Given** a tenant is being created  
**When** the system generates the tenant record  
**Then** a unique, immutable TenantID (UUID7) is auto-generated and stored  
**And** the TenantID cannot be modified after creation

**Verification:**
- TenantID is a primary key UUID7 field
- No API endpoint allows TenantID modification
- Database constraints prevent TenantID updates

---

### AC2: Tenant Status Enumeration
**Given** the Tenant entity exists  
**When** reviewing the status field  
**Then** it supports the following states:
- `Active` - Tenant can access all APIs
- `Suspended` - Tenant access is blocked
- `Inactive` - Tenant is soft-deleted

**And** the status field is stored as an enum or constrained string  
**And** default status on creation is `Active`

**Verification:**
- Database schema includes status field with defined values
- Status transitions are validated (e.g., cannot go from Inactive to Active without admin action)
- Default value is properly set

---

### AC3: Core Tenant Attributes
**Given** a tenant record  
**When** examining the entity structure  
**Then** it includes the following core fields:

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID7 | Primary key, immutable, auto-generated | Unique tenant identifier |
| `name` | String | Max 255 chars, indexed, required | Human-readable tenant name |
| `slug` | String | Max 63 chars, unique, indexed, required, immutable | PostgreSQL schema identifier |
| `is_active` | Boolean | Default true | Legacy status flag (consider migrating to status enum) |

**Verification:**
- All fields exist in database schema
- Field constraints are enforced at database level
- Indexes are created for performance

---

### AC4: Audit Trail Fields
**Given** any tenant operation (create, update, delete)  
**When** the operation completes  
**Then** the following audit fields are automatically populated:

| Field | Type | Auto-populated | Description |
|-------|------|----------------|-------------|
| `created_at` | DateTime (UTC) | On creation | Timestamp of tenant creation |
| `updated_at` | DateTime (UTC) | On update | Timestamp of last modification |
| `created_by` | String (255) | On creation | User/admin who created tenant |
| `updated_by` | String (255) | On update | User/admin who last modified tenant |

**And** timestamps use UTC timezone  
**And** user context is captured from the authenticated session

**Verification:**
- Base model inheritance provides audit fields
- Fields are automatically set without manual intervention
- Timezone handling is consistent

---

### AC5: Configuration and Branding References
**Given** a tenant may have custom configuration and branding  
**When** designing the entity structure  
**Then** provision for future references to:
- Configuration settings (e.g., workflow rules, validation policies)
- Branding assets (e.g., logo, colors, custom messaging)

**Note:** Implementation may be deferred to future stories, but structure should allow extension

**Verification:**
- Documentation indicates how to extend tenant with config/branding
- Database schema allows adding foreign keys or JSON fields in future
- No breaking changes required when adding these features

---

### AC6: Database Schema Definition
**Given** the Tenant entity design  
**When** creating the database schema  
**Then**:
- Table is created in the `public` schema
- Table name is `tenants`
- All constraints (unique, not null, indexes) are properly defined
- Alembic migration exists for schema creation
- Migration is reversible (down migration exists)

**Verification:**
- Run migration successfully on clean database
- Verify schema with `\d+ public.tenants`
- Test rollback migration
- Check foreign key relationships if any

---

### AC7: Schema-Based Multi-Tenancy Support
**Given** each tenant needs data isolation  
**When** a tenant is created  
**Then** the `slug` field serves as the PostgreSQL schema name  
**And** the slug follows PostgreSQL identifier rules (max 63 chars, valid characters)  
**And** a corresponding PostgreSQL schema is provisioned using the slug

**Verification:**
- Slug validation prevents invalid schema names
- Schema provisioning creates `tenant_{slug}` schema
- Schema contains tenant-specific tables
- No cross-tenant data leakage is possible

---

## Technical Implementation Notes

### Current Implementation Status
✅ **Already Implemented:**
- `Tenant` model in `app/models/public/tenant.py`
- UUID7 auto-generation for `id`
- Audit fields via `PublicSchemaModel` inheritance
- `is_active` boolean flag
- Unique slug with schema naming

⚠️ **Needs Enhancement:**
- Migrate from `is_active` boolean to proper status enum
- Add status transition validation
- Enhance slug validation
- Add database-level constraints for status

### Database Schema
```sql
-- public.tenants table structure
CREATE TABLE public.tenants (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(63) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    updated_by VARCHAR(255) NOT NULL
);

CREATE INDEX idx_tenants_name ON public.tenants(name);
CREATE INDEX idx_tenants_slug ON public.tenants(slug);

-- Future: Add status enum
-- ALTER TABLE public.tenants ADD COLUMN status VARCHAR(20) DEFAULT 'Active';
-- ALTER TABLE public.tenants ADD CONSTRAINT chk_status CHECK (status IN ('Active', 'Suspended', 'Inactive'));
```

---

## Definition of Done

### Checklist
- [ ] Database schema defined with all required fields
- [ ] Status enum defined (Active/Suspended/Inactive)
- [ ] Audit fields (created_at, updated_at, created_by, updated_by) implemented via base model
- [ ] Alembic migration created and tested
- [ ] Migration includes proper indexes
- [ ] Migration includes constraints (unique, not null)
- [ ] Slug validation enforces PostgreSQL identifier rules
- [ ] TenantID immutability enforced at model and API level
- [ ] Documentation updated with entity structure
- [ ] Architecture team review completed
- [ ] All acceptance criteria verified

---

## Dependencies
- `app/models/base.py` - AuditBase and PublicSchemaModel
- `sqlmodel` - ORM framework
- `uuid_extensions` - UUID7 generation
- `alembic` - Database migrations

---

## Test Scenarios

### Test 1: Tenant Creation
```python
def test_tenant_creation_generates_uuid7():
    tenant = Tenant(name="Acme Corp", slug="acme")
    assert tenant.id is not None
    assert isinstance(tenant.id, UUID)
    # Verify UUID7 (first 4 bits should indicate version 7)
```

### Test 2: Immutability
```python
def test_tenant_id_cannot_be_modified():
    tenant = await create_tenant(...)
    original_id = tenant.id
    
    # Attempt to modify ID should fail
    with pytest.raises(ValidationError):
        await update_tenant(tenant.id, {"id": uuid4()})
```

### Test 3: Audit Fields
```python
def test_audit_fields_auto_populate():
    tenant = await create_tenant(...)
    assert tenant.created_at is not None
    assert tenant.created_by == "admin@example.com"
    assert tenant.created_at.tzinfo is not None  # UTC
```

### Test 4: Slug Validation
```python
def test_slug_validation():
    # Valid slug
    tenant = Tenant(name="Test", slug="valid_slug_123")
    assert tenant.slug == "valid_slug_123"
    
    # Invalid slug (too long, >63 chars)
    with pytest.raises(ValidationError):
        Tenant(name="Test", slug="a" * 64)
```

---

## Future Enhancements
1. Add `configuration_id` foreign key to tenant configuration table
2. Add `branding_id` foreign key to branding assets table
3. Implement status transition state machine
4. Add tenant metadata JSON field for extensibility
5. Implement tenant tags/labels for filtering
6. Add tenant tier/plan for SaaS pricing

---

## References
- [Multi-Tenancy Setup and Config](./multi-tenancy-setup-and-config.md)
- PostgreSQL Identifier Rules: https://www.postgresql.org/docs/current/sql-syntax-lexical.html#SQL-SYNTAX-IDENTIFIERS
- UUID7 Specification: https://datatracker.ietf.org/doc/html/draft-ietf-uuidrev-rfc4122bis
