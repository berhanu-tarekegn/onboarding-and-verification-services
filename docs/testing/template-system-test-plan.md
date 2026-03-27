# Template System Test Plan

## Overview

This document provides comprehensive test scenarios for the **Template and Baseline Template** feature implementation. The system supports KYC/verification workflows through a multi-tenant template architecture with baseline templates (system-managed) and tenant templates (tenant-customizable).

**Testing Focus:**
- Baseline template management (CRUD operations)
- Tenant template management (standalone and baseline extension)
- Version control and activation
- Multi-tenancy isolation
- Data integrity and audit trails

---

## System Architecture Summary

### Template Types

1. **Baseline Templates** (System-owned)
   - Live in `public` schema
   - Created/managed by platform admins only
   - Read-only for tenants
   - Can be extended by tenants
   - Support versioning with active version pointer

2. **Tenant Templates** (Tenant-owned)
   - Live in per-tenant schemas (`tenant_<slug>`)
   - Two modes:
     - **Standalone**: Complete template created from scratch
     - **Extension**: Extends a baseline template with customizations
   - Support versioning with active version pointer
   - Isolated per tenant

### Key Concepts

- **Header/Definition Pattern**: Templates have a header (metadata) and multiple versioned definitions (configuration)
- **Version Locking**: Submissions lock to specific template version IDs to maintain data integrity
- **Schema Isolation**: Tenant data is completely isolated using PostgreSQL schemas
- **Active Version**: Each template points to one active version used for new submissions

---

## Test Environment Setup

### Prerequisites

- [ ] Application running on local/test environment
- [ ] At least 2 test tenants provisioned (e.g., `tenant_alpha`, `tenant_beta`)
- [ ] Platform admin credentials available
- [ ] API testing tool configured (Postman, Bruno, or curl)
- [ ] Database access for validation queries

### Test Tenants

Create these test tenants before testing:

```bash
# Tenant 1: Alpha Bank
POST /v1/tenants
{
  "name": "Alpha Bank",
  "slug": "alpha",
  "is_active": true
}

# Tenant 2: Beta Financial
POST /v1/tenants
{
  "name": "Beta Financial",
  "slug": "beta",
  "is_active": true
}
```

### Test Data Cleanup

Before each test suite, clean up:
```sql
-- Clean tenant schemas
DROP SCHEMA IF EXISTS tenant_alpha CASCADE;
DROP SCHEMA IF EXISTS tenant_beta CASCADE;

-- Clean public baseline templates
DELETE FROM public.baseline_template_definitions;
DELETE FROM public.baseline_templates;

-- Recreate tenant schemas
-- (re-run tenant provisioning or migrations)
```

---

## Test Scenarios

## Section 1: Baseline Template Management

### Test Case 1.1: Create Baseline Template (Happy Path)

**Objective:** Verify platform admin can create a baseline template

**Prerequisites:**
- Platform admin authentication
- No X-Tenant-ID header required (public endpoint)

**Test Steps:**

1. **Create baseline template**
   ```http
   POST /v1/baseline-templates
   Content-Type: application/json
   
   {
     "name": "Basic KYC Template",
     "description": "Standard KYC verification for individual accounts",
     "category": "kyc",
     "is_active": true
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - Response includes:
     - `id` (UUID)
     - `name`: "Basic KYC Template"
     - `description`: matches request
     - `category`: "kyc"
     - `is_active`: true
     - `is_locked`: false (default)
     - `active_version_id`: null (no versions yet)
     - `created_at`, `updated_at` (ISO timestamps)
     - `created_by`, `updated_by` (user identifiers)

3. **Database validation**
   ```sql
   SELECT * FROM public.baseline_templates 
   WHERE name = 'Basic KYC Template';
   ```
   - Record exists with matching data
   - Audit fields populated

**Expected Result:** ✅ Baseline template created successfully

---

### Test Case 1.2: Create Baseline Template Version

**Objective:** Verify adding versioned configuration to baseline template

**Prerequisites:**
- Baseline template exists (from Test 1.1)
- Template ID available

**Test Steps:**

1. **Create first version**
   ```http
   POST /v1/baseline-templates/{template_id}/versions
   Content-Type: application/json
   
   {
     "version_tag": "v1.0.0",
     "form_schema": {
       "steps": [
         {
           "id": "personal_info",
           "title": "Personal Information",
           "fields": [
             {
               "id": "full_name",
               "type": "text",
               "label": "Full Name",
               "required": true,
               "validation": {
                 "min_length": 2,
                 "max_length": 100
               }
             },
             {
               "id": "date_of_birth",
               "type": "date",
               "label": "Date of Birth",
               "required": true
             }
           ]
         },
         {
           "id": "documents",
           "title": "Document Upload",
           "fields": [
             {
               "id": "id_document",
               "type": "document",
               "label": "ID Document",
               "required": true,
               "document_config": {
                 "accepted_types": ["pdf", "jpg", "png"],
                 "max_size_mb": 5,
                 "verification_required": true
               }
             }
           ]
         }
       ]
     },
     "rules_config": {
       "validation_rules": [
         {
           "rule_id": "age_check",
           "field": "date_of_birth",
           "condition": "age_greater_than",
           "value": 18,
           "error_message": "Must be 18 or older"
         }
       ],
       "auto_approval_conditions": {
         "all_documents_verified": true,
         "no_watchlist_matches": true
       }
     },
     "changelog": "Initial version with personal info and document upload",
     "is_draft": true
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - Response includes:
     - `id` (UUID)
     - `template_id`: matches parent template
     - `version_tag`: "v1.0.0"
     - `form_schema`: matches request
     - `rules_config`: matches request
     - `changelog`: matches request
     - `is_draft`: true
     - Audit fields populated

3. **Verify template header NOT updated yet**
   ```http
   GET /v1/baseline-templates/{template_id}
   ```
   - `active_version_id` is still null (draft not activated)

**Expected Result:** ✅ Version created but not activated

---

### Test Case 1.3: Activate Baseline Template Version

**Objective:** Verify setting a version as active

**Prerequisites:**
- Baseline template with at least one draft version (from Test 1.2)

**Test Steps:**

1. **Activate the version**
   ```http
   POST /v1/baseline-templates/{template_id}/versions/{version_id}/activate
   ```

2. **Verify response**
   - Status: `200 OK`
   - Response shows version with `is_draft`: false (if endpoint updates this)

3. **Verify template header updated**
   ```http
   GET /v1/baseline-templates/{template_id}
   ```
   - `active_version_id`: matches the activated version ID

4. **Database validation**
   ```sql
   SELECT id, active_version_id 
   FROM public.baseline_templates 
   WHERE id = '{template_id}';
   ```
   - `active_version_id` matches activated version

**Expected Result:** ✅ Version activated and template points to it

---

### Test Case 1.4: List Baseline Templates

**Objective:** Verify listing baseline templates with filtering

**Prerequisites:**
- Multiple baseline templates exist with different categories and status

**Test Steps:**

1. **List all active templates**
   ```http
   GET /v1/baseline-templates?is_active=true
   ```
   - Verify only active templates returned
   - Pagination works if implemented

2. **Filter by category**
   ```http
   GET /v1/baseline-templates?category=kyc
   ```
   - Verify only KYC category templates returned

3. **Get single template with versions**
   ```http
   GET /v1/baseline-templates/{template_id}?include_versions=true
   ```
   - Response includes all versions in `versions` array
   - Active version is identified

**Expected Result:** ✅ Filtering and retrieval work correctly

---

### Test Case 1.5: Update Baseline Template Metadata

**Objective:** Verify updating template header without affecting versions

**Prerequisites:**
- Baseline template exists

**Test Steps:**

1. **Update template**
   ```http
   PATCH /v1/baseline-templates/{template_id}
   Content-Type: application/json
   
   {
     "description": "Updated: Standard KYC verification for individual accounts (enhanced)",
     "category": "kyc_enhanced"
   }
   ```

2. **Verify response**
   - Status: `200 OK`
   - Updated fields reflected
   - `updated_at` timestamp changed
   - `updated_by` shows current user

3. **Verify versions unchanged**
   - Existing versions still exist
   - `active_version_id` still points to same version

**Expected Result:** ✅ Metadata updated, versions intact

---

### Test Case 1.6: Deactivate Baseline Template

**Objective:** Verify inactive templates cannot be used by tenants

**Prerequisites:**
- Baseline template exists and is active

**Test Steps:**

1. **Deactivate template**
   ```http
   PATCH /v1/baseline-templates/{template_id}
   {
     "is_active": false
   }
   ```

2. **Verify tenants cannot extend inactive baseline**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   
   {
     "name": "Alpha KYC",
     "extends_baseline_id": "{inactive_template_id}"
   }
   ```
   - Status: `422 Unprocessable Entity` or `403 Forbidden`
   - Error message: "Baseline template is inactive"

**Expected Result:** ✅ Inactive templates blocked from use

---

### Test Case 1.7: Delete Baseline Template (No Dependencies)

**Objective:** Verify deletion when no tenant templates extend it

**Prerequisites:**
- Baseline template exists
- NO tenant templates extend this baseline

**Test Steps:**

1. **Delete template**
   ```http
   DELETE /v1/baseline-templates/{template_id}
   ```

2. **Verify response**
   - Status: `204 No Content` or `200 OK`

3. **Verify template removed**
   ```http
   GET /v1/baseline-templates/{template_id}
   ```
   - Status: `404 Not Found`

**Expected Result:** ✅ Template deleted successfully

---

### Test Case 1.8: Prevent Delete When In Use

**Objective:** Verify deletion blocked when tenant templates depend on baseline

**Prerequisites:**
- Baseline template exists
- At least one tenant template extends this baseline

**Test Steps:**

1. **Create tenant template extending baseline**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   
   {
     "name": "Alpha Custom KYC",
     "extends_baseline_id": "{baseline_template_id}"
   }
   ```

2. **Attempt to delete baseline**
   ```http
   DELETE /v1/baseline-templates/{baseline_template_id}
   ```

3. **Verify deletion blocked**
   - Status: `409 Conflict` or `422 Unprocessable Entity`
   - Error message indicates template is in use
   - Message should NOT reveal which tenants use it (security)

**Expected Result:** ✅ Deletion prevented, clear error message

---

## Section 2: Tenant Template Management (Standalone)

### Test Case 2.1: Create Standalone Tenant Template

**Objective:** Verify tenant can create independent template

**Prerequisites:**
- Tenant provisioned (e.g., `alpha`)
- Tenant authentication configured

**Test Steps:**

1. **Create standalone template**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   Content-Type: application/json
   
   {
     "name": "Alpha Onboarding Form",
     "description": "Custom onboarding for Alpha Bank customers"
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - Response includes:
     - `id` (UUID)
     - `name`: "Alpha Onboarding Form"
     - `extends_baseline_id`: null (standalone)
     - `is_active`: true (default)
     - `active_version_id`: null (no versions yet)
     - Audit fields populated

3. **Database validation**
   ```sql
   SELECT * FROM tenant_alpha.tenant_templates 
   WHERE name = 'Alpha Onboarding Form';
   ```
   - Record exists in tenant schema
   - `extends_baseline_id` is NULL

**Expected Result:** ✅ Standalone template created in tenant schema

---

### Test Case 2.2: Create Tenant Template Version (Standalone)

**Objective:** Verify adding configuration to standalone tenant template

**Prerequisites:**
- Standalone tenant template exists (from Test 2.1)

**Test Steps:**

1. **Create version**
   ```http
   POST /v1/templates/{template_id}/versions
   X-Tenant-ID: alpha
   
   {
     "version_tag": "v1.0.0",
     "form_schema_overrides": {
       "steps": [
         {
           "id": "account_type",
           "title": "Account Selection",
           "fields": [
             {
               "id": "account_type",
               "type": "select",
               "label": "Account Type",
               "required": true,
               "options": [
                 {"value": "savings", "label": "Savings Account"},
                 {"value": "checking", "label": "Checking Account"}
               ]
             }
           ]
         }
       ]
     },
     "rules_config_overrides": {
       "validation_rules": [
         {
           "rule_id": "account_limit",
           "condition": "max_accounts_per_customer",
           "value": 5
         }
       ]
     },
     "changelog": "Initial version with account type selection",
     "is_draft": true
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - Version created with provided configuration

3. **Note:** For standalone templates, `*_overrides` fields contain the COMPLETE configuration (not overrides)

**Expected Result:** ✅ Version created with full configuration

---

### Test Case 2.3: Activate Tenant Template Version

**Objective:** Verify version activation for tenant template

**Prerequisites:**
- Tenant template with draft version

**Test Steps:**

1. **Activate version**
   ```http
   POST /v1/templates/{template_id}/versions/{version_id}/activate
   X-Tenant-ID: alpha
   ```

2. **Verify template updated**
   ```http
   GET /v1/templates/{template_id}
   X-Tenant-ID: alpha
   ```
   - `active_version_id` matches activated version

**Expected Result:** ✅ Version activated successfully

---

### Test Case 2.4: List Tenant Templates (Tenant Isolation)

**Objective:** Verify tenant can only see their own templates

**Prerequisites:**
- Templates created for both `alpha` and `beta` tenants

**Test Steps:**

1. **List Alpha's templates**
   ```http
   GET /v1/templates
   X-Tenant-ID: alpha
   ```
   - Returns only Alpha's templates

2. **List Beta's templates**
   ```http
   GET /v1/templates
   X-Tenant-ID: beta
   ```
   - Returns only Beta's templates
   - Alpha's templates NOT visible

3. **Attempt cross-tenant access**
   ```http
   GET /v1/templates/{alpha_template_id}
   X-Tenant-ID: beta
   ```
   - Status: `403 Forbidden` or `404 Not Found`
   - Beta cannot access Alpha's template

**Expected Result:** ✅ Complete tenant isolation enforced

---

### Test Case 2.5: Update Tenant Template

**Objective:** Verify tenant can update their template metadata

**Prerequisites:**
- Tenant template exists

**Test Steps:**

1. **Update template**
   ```http
   PATCH /v1/templates/{template_id}
   X-Tenant-ID: alpha
   
   {
     "description": "Updated: Custom onboarding with enhanced features",
     "is_active": false
   }
   ```

2. **Verify response**
   - Status: `200 OK`
   - Changes reflected
   - `updated_at` changed

**Expected Result:** ✅ Template updated successfully

---

### Test Case 2.6: Delete Tenant Template

**Objective:** Verify tenant can delete unused template

**Prerequisites:**
- Tenant template exists
- NO submissions using this template

**Test Steps:**

1. **Delete template**
   ```http
   DELETE /v1/templates/{template_id}
   X-Tenant-ID: alpha
   ```

2. **Verify response**
   - Status: `204 No Content`

3. **Verify removal**
   ```http
   GET /v1/templates/{template_id}
   X-Tenant-ID: alpha
   ```
   - Status: `404 Not Found`

**Expected Result:** ✅ Template deleted

---

## Section 3: Tenant Template Extension (Baseline)

### Test Case 3.1: Create Tenant Template Extending Baseline

**Objective:** Verify tenant can extend baseline template

**Prerequisites:**
- Active baseline template exists with activated version

**Test Steps:**

1. **Create extension template**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   
   {
     "name": "Alpha Enhanced KYC",
     "description": "Extends basic KYC with Alpha-specific requirements",
     "extends_baseline_id": "{baseline_template_id}"
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - `extends_baseline_id`: matches baseline template
   - Template created in tenant schema

3. **Database validation**
   ```sql
   SELECT id, name, extends_baseline_id 
   FROM tenant_alpha.tenant_templates 
   WHERE name = 'Alpha Enhanced KYC';
   ```
   - `extends_baseline_id` references public baseline template

**Expected Result:** ✅ Extension template created with baseline reference

---

### Test Case 3.2: Create Version with Overrides (Extension Template)

**Objective:** Verify tenant can customize baseline with overrides

**Prerequisites:**
- Tenant template extending baseline exists
- Baseline has active version with form_schema

**Test Steps:**

1. **Create version with overrides**
   ```http
   POST /v1/templates/{extension_template_id}/versions
   X-Tenant-ID: alpha
   
   {
     "version_tag": "v1.0.0",
     "form_schema_overrides": {
       "steps": [
         {
           "id": "additional_info",
           "title": "Alpha-Specific Information",
           "fields": [
             {
               "id": "customer_segment",
               "type": "select",
               "label": "Customer Segment",
               "required": true,
               "options": [
                 {"value": "retail", "label": "Retail"},
                 {"value": "premium", "label": "Premium"},
                 {"value": "corporate", "label": "Corporate"}
               ]
             }
           ]
         }
       ]
     },
     "rules_config_overrides": {
       "risk_scoring": {
         "premium_customer_bonus": -10,
         "corporate_enhanced_check": true
       }
     },
     "changelog": "Added Alpha customer segmentation",
     "is_draft": true
   }
   ```

2. **Verify response**
   - Status: `201 Created`
   - Overrides stored

3. **Note:** Service layer should merge these overrides with baseline when resolved

**Expected Result:** ✅ Version with overrides created

---

### Test Case 3.3: Retrieve Merged Configuration (Extension Template)

**Objective:** Verify system merges baseline + tenant overrides correctly

**Prerequisites:**
- Extension template with active version
- Baseline template with active version

**Test Steps:**

1. **Get resolved template configuration**
   ```http
   GET /v1/templates/{extension_template_id}/resolved-config
   X-Tenant-ID: alpha
   ```

2. **Verify response contains:**
   - Baseline form_schema fields
   - + Tenant override fields
   - Baseline rules_config
   - + Tenant override rules
   - Clear indication of which version IDs were used

3. **Verify merge logic:**
   - Tenant additions appear
   - Tenant overrides replace baseline fields where keys match
   - Baseline fields not overridden remain

**Expected Result:** ✅ Merged configuration correct

---

### Test Case 3.4: Baseline Update Reflects in Extension

**Objective:** Verify tenant sees baseline changes automatically

**Prerequisites:**
- Baseline template with active version v1.0.0
- Tenant extension template using that baseline

**Test Steps:**

1. **Create new baseline version**
   ```http
   POST /v1/baseline-templates/{baseline_id}/versions
   
   {
     "version_tag": "v2.0.0",
     "form_schema": {
       "steps": [
         {
           "id": "personal_info",
           "fields": [
             {
               "id": "full_name",
               "type": "text",
               "label": "Full Legal Name",
               "required": true
             },
             {
               "id": "email",
               "type": "email",
               "label": "Email Address",
               "required": true
             }
           ]
         }
       ]
     },
     "rules_config": {},
     "changelog": "Added email field",
     "is_draft": false
   }
   ```

2. **Activate new baseline version**
   ```http
   POST /v1/baseline-templates/{baseline_id}/versions/{v2_id}/activate
   ```

3. **Retrieve tenant extension resolved config**
   ```http
   GET /v1/templates/{extension_template_id}/resolved-config
   X-Tenant-ID: alpha
   ```

4. **Verify:**
   - New baseline fields (email) appear in merged config
   - Tenant overrides still applied
   - Baseline version ID in response is v2.0.0

**Expected Result:** ✅ Baseline updates automatically inherited

---

### Test Case 3.5: Tenant Cannot Modify Baseline Directly

**Objective:** Verify tenants have read-only access to baselines

**Prerequisites:**
- Baseline template exists
- Tenant authentication

**Test Steps:**

1. **Attempt to update baseline as tenant**
   ```http
   PATCH /v1/baseline-templates/{baseline_id}
   X-Tenant-ID: alpha
   
   {
     "description": "Attempted modification"
   }
   ```
   - Status: `403 Forbidden`
   - Error: "Baseline templates are read-only for tenants"

2. **Attempt to delete baseline as tenant**
   ```http
   DELETE /v1/baseline-templates/{baseline_id}
   X-Tenant-ID: alpha
   ```
   - Status: `403 Forbidden`

**Expected Result:** ✅ All modification attempts blocked

---

## Section 4: Version Control & History

### Test Case 4.1: Multiple Versions Coexist

**Objective:** Verify multiple versions can exist for one template

**Prerequisites:**
- Template exists (baseline or tenant)

**Test Steps:**

1. **Create version v1.0.0**
   ```http
   POST /v1/.../versions
   {"version_tag": "v1.0.0", ...}
   ```

2. **Create version v1.1.0**
   ```http
   POST /v1/.../versions
   {"version_tag": "v1.1.0", ...}
   ```

3. **Create version v2.0.0**
   ```http
   POST /v1/.../versions
   {"version_tag": "v2.0.0", ...}
   ```

4. **List all versions**
   ```http
   GET /v1/.../versions
   ```
   - All three versions returned
   - Each has unique ID
   - Only one is active (or none if all drafts)

**Expected Result:** ✅ Multiple versions coexist

---

### Test Case 4.2: Switch Active Version

**Objective:** Verify changing active version

**Prerequisites:**
- Template with multiple versions (v1.0.0 active, v2.0.0 available)

**Test Steps:**

1. **Current state**
   ```http
   GET /v1/templates/{template_id}
   ```
   - `active_version_id`: v1.0.0

2. **Activate v2.0.0**
   ```http
   POST /v1/templates/{template_id}/versions/{v2_id}/activate
   X-Tenant-ID: alpha
   ```

3. **Verify switch**
   ```http
   GET /v1/templates/{template_id}
   ```
   - `active_version_id`: now v2.0.0 ID

4. **Verify old version still exists**
   ```http
   GET /v1/templates/{template_id}/versions/{v1_id}
   ```
   - v1.0.0 still retrievable (historical)

**Expected Result:** ✅ Active version switched, history preserved

---

### Test Case 4.3: Version Immutability (After Activation)

**Objective:** Verify activated versions cannot be modified

**Prerequisites:**
- Template version activated (is_draft: false)

**Test Steps:**

1. **Attempt to update activated version**
   ```http
   PATCH /v1/.../versions/{activated_version_id}
   
   {
     "form_schema": {...}
   }
   ```

2. **Verify update blocked**
   - Status: `400 Bad Request` or `409 Conflict`
   - Error: "Cannot modify activated version"

3. **Correct workflow:**
   - Must create NEW version
   - Then activate new version

**Expected Result:** ✅ Activated versions immutable

---

## Section 5: Audit & Compliance

### Test Case 5.1: Audit Fields Populated

**Objective:** Verify all audit fields automatically populated

**Prerequisites:**
- User context available (created_by, updated_by)

**Test Steps:**

1. **Create template**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   Authorization: Bearer {token_for_user_alice}
   
   {"name": "Test Template"}
   ```

2. **Verify audit fields**
   - `created_at`: present, ISO timestamp
   - `updated_at`: present, matches created_at initially
   - `created_by`: "alice" (or user identifier)
   - `updated_by`: "alice"

3. **Update template**
   ```http
   PATCH /v1/templates/{template_id}
   X-Tenant-ID: alpha
   Authorization: Bearer {token_for_user_bob}
   
   {"description": "Updated by Bob"}
   ```

4. **Verify audit fields updated**
   - `created_at`: unchanged
   - `updated_at`: new timestamp (> created_at)
   - `created_by`: still "alice"
   - `updated_by`: now "bob"

**Expected Result:** ✅ Audit trail complete and accurate

---

### Test Case 5.2: Version History Preserved

**Objective:** Verify historical versions remain queryable

**Prerequisites:**
- Template with multiple versions over time

**Test Steps:**

1. **Create submission using v1.0.0**
   - (Assumes submission API exists)
   - Submission locks to version ID

2. **Activate v2.0.0**

3. **Retrieve submission**
   - Submission still references v1.0.0 version ID
   - v1.0.0 configuration still retrievable

4. **Query historical version**
   ```http
   GET /v1/templates/{template_id}/versions/{v1_id}
   ```
   - Returns v1.0.0 configuration even though not active

**Expected Result:** ✅ Version history preserved for compliance

---

## Section 6: Error Handling & Edge Cases

### Test Case 6.1: Missing X-Tenant-ID Header

**Objective:** Verify tenant-scoped endpoints require header

**Test Steps:**

1. **Call tenant endpoint without header**
   ```http
   GET /v1/templates
   # X-Tenant-ID header MISSING
   ```

2. **Verify error**
   - Status: `400 Bad Request`
   - Error: "X-Tenant-ID header is required"

**Expected Result:** ✅ Clear error for missing tenant ID

---

### Test Case 6.2: Invalid Tenant ID

**Objective:** Verify invalid tenant rejected

**Test Steps:**

1. **Use non-existent tenant**
   ```http
   GET /v1/templates
   X-Tenant-ID: nonexistent_tenant
   ```

2. **Verify error**
   - Status: `404 Not Found`
   - Error: "Tenant not found"

**Expected Result:** ✅ Invalid tenant rejected

---

### Test Case 6.3: Inactive Tenant

**Objective:** Verify inactive tenants cannot access APIs

**Prerequisites:**
- Tenant exists but is_active = false

**Test Steps:**

1. **Deactivate tenant**
   ```http
   PATCH /v1/tenants/{tenant_id}
   {"is_active": false}
   ```

2. **Attempt to use tenant APIs**
   ```http
   GET /v1/templates
   X-Tenant-ID: alpha
   ```

3. **Verify error**
   - Status: `403 Forbidden`
   - Error: "Tenant is inactive"

**Expected Result:** ✅ Inactive tenant access blocked

---

### Test Case 6.4: Duplicate Template Names (Within Tenant)

**Objective:** Verify name uniqueness enforced per tenant

**Test Steps:**

1. **Create template**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   {"name": "Standard KYC"}
   ```

2. **Attempt duplicate name**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   {"name": "Standard KYC"}
   ```

3. **Verify error**
   - Status: `409 Conflict`
   - Error: "Template name already exists"

**Expected Result:** ✅ Duplicate names prevented

---

### Test Case 6.5: Cross-Tenant Name Collision (Allowed)

**Objective:** Verify different tenants CAN use same template name

**Test Steps:**

1. **Alpha creates template**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   {"name": "Standard KYC"}
   ```

2. **Beta creates same name**
   ```http
   POST /v1/templates
   X-Tenant-ID: beta
   {"name": "Standard KYC"}
   ```

3. **Verify both succeed**
   - Both return 201 Created
   - Different IDs
   - Isolated per tenant

**Expected Result:** ✅ Same name allowed across tenants

---

### Test Case 6.6: Extend Non-Existent Baseline

**Objective:** Verify validation when extending invalid baseline

**Test Steps:**

1. **Create extension with fake baseline ID**
   ```http
   POST /v1/templates
   X-Tenant-ID: alpha
   
   {
     "name": "Extension Test",
     "extends_baseline_id": "00000000-0000-0000-0000-000000000000"
   }
   ```

2. **Verify error**
   - Status: `404 Not Found` or `422 Unprocessable Entity`
   - Error: "Baseline template not found"

**Expected Result:** ✅ Invalid baseline reference rejected

---

### Test Case 6.7: Activate Version Without Configuration

**Objective:** Verify cannot activate empty version

**Test Steps:**

1. **Create minimal version**
   ```http
   POST /v1/templates/{template_id}/versions
   X-Tenant-ID: alpha
   
   {
     "version_tag": "v1.0.0",
     "form_schema_overrides": {},
     "rules_config_overrides": {}
   }
   ```

2. **Attempt activation**
   ```http
   POST /v1/templates/{template_id}/versions/{version_id}/activate
   X-Tenant-ID: alpha
   ```

3. **Expected behavior:**
   - **Option A**: Activation blocked with validation error
   - **Option B**: Activation allowed (business decision)
   - Document actual behavior

**Expected Result:** ✅ Behavior matches business rules

---

## Section 7: Performance & Load Testing

### Test Case 7.1: List Templates Performance

**Objective:** Verify listing performs well with many templates

**Test Setup:**
- Create 100 templates in one tenant

**Test Steps:**

1. **Measure list performance**
   ```http
   GET /v1/templates?limit=50&offset=0
   X-Tenant-ID: alpha
   ```

2. **Verify:**
   - Response time < 500ms
   - Pagination works
   - Correct count returned

**Expected Result:** ✅ Acceptable performance

---

### Test Case 7.2: Concurrent Template Creation

**Objective:** Verify system handles concurrent requests

**Test Steps:**

1. **Send 10 simultaneous template creation requests**
   - Use different names
   - Same tenant

2. **Verify:**
   - All succeed or fail appropriately
   - No deadlocks
   - No duplicate IDs
   - All audit fields correct

**Expected Result:** ✅ Concurrent operations handled safely

---

## Section 8: Integration Testing

### Test Case 8.1: Template to Submission Flow

**Objective:** Verify end-to-end template usage in submissions

**Prerequisites:**
- Template with active version exists
- Submission API available

**Test Steps:**

1. **Create template with version**
   - (Use previous test cases)

2. **Activate version**

3. **Create submission using template**
   ```http
   POST /v1/submissions
   X-Tenant-ID: alpha
   
   {
     "template_id": "{template_id}",
     "form_data": {
       "full_name": "John Doe",
       "date_of_birth": "1990-01-15"
     }
   }
   ```

4. **Verify submission locks version**
   - Submission references `template_version_id`
   - If template extends baseline, `baseline_version_id` captured

5. **Update template (new version)**

6. **Verify old submission still references old version**
   - Historical data integrity maintained

**Expected Result:** ✅ Template-submission integration works

---

### Test Case 8.2: Baseline Extension Chain

**Objective:** Verify baseline → tenant extension → submission chain

**Test Steps:**

1. **Create baseline template**
2. **Create baseline version (v1.0.0)**
3. **Activate baseline version**
4. **Tenant creates extension template**
5. **Tenant creates extension version with overrides**
6. **Activate tenant version**
7. **Create submission using tenant template**

8. **Verify submission captures:**
   - `template_id`: tenant template ID
   - `template_version_id`: tenant version ID
   - `baseline_version_id`: baseline version ID (if tracked)

9. **Update baseline to v2.0.0**

10. **Verify:**
    - Existing submission still references v1.0.0
    - New submissions use v2.0.0

**Expected Result:** ✅ Full extension chain works correctly

---

## Test Execution Checklist

### Pre-Testing
- [ ] Test environment configured
- [ ] Test tenants provisioned
- [ ] Database backup taken
- [ ] API documentation reviewed
- [ ] Test data scripts prepared

### Baseline Template Tests
- [ ] Test 1.1: Create baseline template
- [ ] Test 1.2: Create baseline version
- [ ] Test 1.3: Activate baseline version
- [ ] Test 1.4: List baseline templates
- [ ] Test 1.5: Update baseline metadata
- [ ] Test 1.6: Deactivate baseline
- [ ] Test 1.7: Delete baseline (no dependencies)
- [ ] Test 1.8: Prevent delete when in use

### Tenant Template Tests (Standalone)
- [ ] Test 2.1: Create standalone template
- [ ] Test 2.2: Create standalone version
- [ ] Test 2.3: Activate version
- [ ] Test 2.4: List templates (isolation)
- [ ] Test 2.5: Update template
- [ ] Test 2.6: Delete template

### Tenant Template Tests (Extension)
- [ ] Test 3.1: Create extension template
- [ ] Test 3.2: Create version with overrides
- [ ] Test 3.3: Retrieve merged configuration
- [ ] Test 3.4: Baseline update reflects
- [ ] Test 3.5: Tenant cannot modify baseline

### Version Control Tests
- [ ] Test 4.1: Multiple versions coexist
- [ ] Test 4.2: Switch active version
- [ ] Test 4.3: Version immutability

### Audit Tests
- [ ] Test 5.1: Audit fields populated
- [ ] Test 5.2: Version history preserved

### Error Handling Tests
- [ ] Test 6.1: Missing tenant header
- [ ] Test 6.2: Invalid tenant ID
- [ ] Test 6.3: Inactive tenant
- [ ] Test 6.4: Duplicate names (within tenant)
- [ ] Test 6.5: Cross-tenant name collision
- [ ] Test 6.6: Extend non-existent baseline
- [ ] Test 6.7: Activate empty version

### Performance Tests
- [ ] Test 7.1: List performance
- [ ] Test 7.2: Concurrent operations

### Integration Tests
- [ ] Test 8.1: Template to submission flow
- [ ] Test 8.2: Baseline extension chain

### Post-Testing
- [ ] All test results documented
- [ ] Defects logged with severity
- [ ] Test coverage report generated
- [ ] Sign-off obtained

---

## Test Result Template

For each test, document:

```
Test ID: [e.g., 1.1]
Test Name: [e.g., Create Baseline Template]
Date: [YYYY-MM-DD]
Tester: [Name]
Environment: [dev/staging/prod]

Status: [PASS / FAIL / BLOCKED]

Steps Executed:
1. [Step 1 with actual values]
2. [Step 2 with actual values]

Actual Result:
[What happened]

Expected Result:
[What should happen]

Defects:
[Link to bug tickets if failed]

Notes:
[Any additional observations]
```

---

## Defect Severity Guidelines

- **Critical**: System crash, data loss, security breach, tenant isolation broken
- **High**: Feature not working, incorrect data, major functionality blocked
- **Medium**: Partial functionality issue, workaround available
- **Low**: UI/UX issue, minor inconsistency, documentation error

---

## Key Success Metrics

- ✅ **100% tenant isolation** - No cross-tenant data leaks
- ✅ **Version integrity** - Historical versions preserved and queryable
- ✅ **Baseline immutability** - Tenants cannot modify baselines
- ✅ **Audit completeness** - All operations tracked with who/when
- ✅ **Error handling** - Clear, actionable error messages
- ✅ **Performance** - Sub-second response times for standard operations

---

## Contact & Support

- **Development Team**: [Contact info]
- **Test Environment Issues**: [Support channel]
- **Bug Reporting**: [Issue tracker URL]
- **Documentation**: [Wiki/docs URL]

---

**Document Version:** 1.0  
**Last Updated:** [Date]  
**Approved By:** [Name/Role]
