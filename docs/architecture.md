# Onboarding & Verification SaaS — Architecture Documentation

## 1. System Architecture

A **multi-tenant, workflow-driven onboarding & KYC verification platform** that lets banks (tenants) customize customer onboarding flows without code changes.

```mermaid
graph TB
    subgraph "Engagement Layer (Front-end)"
        SDK[Bank Mobile App / SDK]
        AdminPortal[Tenant Admin Portal]
    end

    API_GW[API Gateway / Load Balancer]

    subgraph "Orchestration & Logic Layer (Back-end)"
        direction TB
        TemplateSvc[Template Service<br/>FastAPI + SQLModel]
        DecisionEng[Decision Engine<br/>Stateless Rule Evaluation]
        
        subgraph "Temporal Cluster (The Core)"
            WorkflowEng[Workflow Engine]
        end

        subgraph "Verification Layer"
            DocVerify[Document Verification]
            FaceMatch[Face Match / Liveness]
            BizRules[Business Rules Check]
        end

        IntegrationHub[Integration Hub<br/>3rd-Party Adapter Layer]
    end

    subgraph "Data Layer (Persistence)"
        TenantDB[(Tenant Config DB<br/>PostgreSQL Multi-Schema)]
        OpsDB[(Operational DB<br/>Workflow State & Logs)]
        Vault[(PII Vault<br/>Encrypted Storage)]
    end

    subgraph "External World"
        ProviderA[Provider A<br/>e.g., Onfido]
        ProviderB[Provider B<br/>e.g., SumSub]
        BankCore[Bank Core System<br/>Webhook Listener]
    end

    %% Engagement → Gateway
    SDK -->|REST| API_GW
    AdminPortal -->|REST| API_GW

    %% Gateway → Backend
    API_GW -->|Get Config / Start| TemplateSvc
    API_GW -->|Start / Signal Workflow| WorkflowEng

    %% Workflow → Orchestration
    WorkflowEng -->|Get Pinned Rules| TemplateSvc
    WorkflowEng -->|Evaluate Results| DecisionEng
    WorkflowEng -->|Notify Final Status| BankCore

    %% Workflow → Verification Services
    WorkflowEng -->|Activity: Verify| DocVerify
    WorkflowEng -->|Activity: Verify| FaceMatch
    WorkflowEng -->|Activity: Verify| BizRules

    %% Verification → Integration Hub (optional delegation)
    DocVerify -.->|Delegate| IntegrationHub
    FaceMatch -.->|Delegate| IntegrationHub
    BizRules -.->|Delegate| IntegrationHub

    %% Integration Hub → External
    IntegrationHub -->|Async API Call| ProviderA
    IntegrationHub -->|Async API Call| ProviderB
    ProviderA -- Webhook Callback --> API_GW
    ProviderB -- Webhook Callback --> API_GW

    %% Data Connections
    TemplateSvc --> TenantDB
    WorkflowEng --> OpsDB
    WorkflowEng -- Save Sensitive Data --> Vault
    AdminPortal -- Signal Approval --> WorkflowEng

    classDef service fill:#d4e1f5,stroke:#333,stroke-width:2px;
    classDef verify fill:#d5f5d4,stroke:#333,stroke-width:2px;
    classDef db fill:#e1d5e7,stroke:#333,stroke-width:2px;
    classDef external fill:#f5f5f5,stroke:#333,stroke-width:2px,stroke-dasharray: 5 5;
    class WorkflowEng,TemplateSvc,DecisionEng,IntegrationHub,API_GW service;
    class DocVerify,FaceMatch,BizRules verify;
    class TenantDB,OpsDB,Vault db;
    class ProviderA,ProviderB,BankCore external;
```

---

## 2. Component Interactions

### 2.1 Engagement Layer → API Gateway

| From | To | Protocol | Purpose |
|---|---|---|---|
| Bank Mobile App / SDK | API Gateway | REST | Submit onboarding data, fetch form schemas |
| Tenant Admin Portal | API Gateway | REST | Configure templates, approve/reject flagged cases |

### 2.2 API Gateway → Orchestration Layer

| From | To | Purpose |
|---|---|---|
| API Gateway | Template Service | Fetch active `TemplateDefinition`, get form schema JSON |
| API Gateway | Workflow Engine | Start new workflows, signal workflows with user data or webhook results |

### 2.3 Workflow Engine → All Services

The Workflow Engine is the **central orchestrator**. It never calls external APIs directly — it delegates all work through Activities.

| From | To | Interaction Type | Purpose |
|---|---|---|---|
| Workflow Engine | Template Service | Activity | Get pinned rules config for the definition the user started with |
| Workflow Engine | Verification Services | Activity | Trigger document, face, or business rule verification |
| Workflow Engine | Decision Engine | Activity | Evaluate all verification results against tenant rules → verdict |
| Workflow Engine | Bank Core System | Activity | POST final onboarding status (approve/reject) via webhook |
| Admin Portal | Workflow Engine | Signal | Approve or reject a manually-flagged case |

### 2.4 Verification Services → Integration Hub

Custom Verification Services contain **Kifiya-built verification logic**. They decide at runtime whether to handle verification internally or delegate to a third-party provider via the Integration Hub.

| From | To | When | Purpose |
|---|---|---|---|
| Document Verification | Integration Hub | OCR confidence is low | Delegate to Onfido for higher-confidence document check |
| Face Match / Liveness | Integration Hub | Regulated liveness required | Delegate to SumSub for certified liveness detection |
| Business Rules Check | Integration Hub | External watchlist needed | Delegate for sanctions/PEP screening |

> **Key design point:** The Workflow Engine doesn't know or care whether verification happens internally or externally. It calls a Verification Service activity and gets back a normalized result.

### 2.5 Integration Hub → External World

| From | To | Protocol | Purpose |
|---|---|---|---|
| Integration Hub | Provider A (Onfido) | Async REST | Start verification check |
| Integration Hub | Provider B (SumSub) | Async REST | Start verification check |
| Provider A | API Gateway | Webhook | Return verification results |
| Provider B | API Gateway | Webhook | Return verification results |

### 2.6 Data Connections

| From | To | Purpose |
|---|---|---|
| Template Service | Tenant Config DB | Read/write template definitions, form schemas, rules (per-tenant schema) |
| Workflow Engine | Operational DB | Workflow execution state, task history, audit logs |
| Workflow Engine | PII Vault | Store/retrieve sensitive user data (ID photos, SSN, etc.) |

---

## 3. Onboarding Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    participant UserApp as Mobile App (SDK)
    participant Gateway as API Gateway
    participant TplSvc as Template Service
    participant Temporal as Temporal Workflow
    participant VerifySvc as Verification Service
    participant IntHub as Integration Hub
    participant Provider as External Provider
    participant Decision as Decision Engine
    participant Webhook as Bank Core Webhook

    note over UserApp, Gateway: Phase 1: Initialization & UI Fetch
    UserApp->>Gateway: POST /onboarding/init (BankID)
    Gateway->>TplSvc: Get Active TemplateDefinition ID for BankID
    TplSvc-->>Gateway: Return Definition ID (e.g., v1.2)
    Gateway->>Temporal: Start Workflow (Input: UserID, Pinned Definition ID)
    Temporal-->>Gateway: Return WorkflowID
    Gateway->>TplSvc: Get Form Schema for Definition ID
    TplSvc-->>Gateway: Return JSON UI Schema
    Gateway-->>UserApp: Return UI Schema to render

    note over UserApp, Provider: Phase 2: Data Submission & Verification
    UserApp->>Gateway: POST /submit (Documents, Data)
    Gateway->>Temporal: Signal Workflow with User Data
    Temporal->>VerifySvc: Activity: Verify (type, data, tenant_config)
    
    alt Handled Internally
        VerifySvc-->>Temporal: Return internal result
    else Delegates to 3rd Party
        VerifySvc->>IntHub: Delegate to provider
        IntHub->>Provider: Async API Call (Start Check)
        IntHub-->>VerifySvc: Acknowledge started
        note right of Provider: ...Wait for Provider Processing...
        Provider->>Gateway: Webhook Callback (Results)
        Gateway->>Temporal: Signal Workflow with Results
    end

    note over Temporal, Webhook: Phase 3: Decision & Finalization
    Temporal->>TplSvc: Activity: Get Rules Config for Pinned Definition ID
    TplSvc-->>Temporal: Return Rules JSON
    Temporal->>Decision: Activity: Evaluate (Results + Rules JSON)
    Decision-->>Temporal: Return Verdict (e.g., "Level_2_Approved")

    alt Case: Auto-Approved
        Temporal->>Webhook: POST Final Status (UserID, Level_2)
    else Case: Manual Review Required
        Temporal->>Temporal: Enter "Wait for Signal" State
        note right of Temporal: Workflow pauses for days/weeks until Admin acts
    end
```

| Phase | What Happens | Key Design Point |
|---|---|---|
| **1. Initialization** | SDK sends bank ID → Template Service resolves active definition → Temporal workflow starts with **pinned** definition ID → form schema returned to SDK | Definition is pinned at workflow start — template updates don't affect in-progress users |
| **2. Verification** | User submits data → workflow calls Verification Service → handles internally or delegates to Integration Hub → results flow back | Fully async & durable — Temporal workflow survives provider delays of hours/days |
| **3. Decision** | Workflow fetches pinned rules → Decision Engine evaluates → auto-approve or escalate to manual review → bank notified | Manual review parks the workflow indefinitely until admin signals |

---

## 4. Gap Analysis

### ✅ Implemented

| Component | Details |
|---|---|
| Template Service | Full CRUD for `Template` + `TemplateDefinition` with versioning |
| Tenant Management | Registration, schema provisioning, Alembic per-schema migrations |
| Multi-Tenant DB | PostgreSQL `search_path` isolation, `X-Tenant-ID` middleware |
| Form Schema Validation | Pydantic-validated field types: text, dropdown, radio, checkbox, date, fileUpload, signature |
| Version Pinning Model | Header/detail pattern with `active_version_id`; immutability on active definitions |
| Database & Migrations | Async PostgreSQL, Alembic, Docker Compose dev |

### ❌ Missing

| # | Component | Priority | Effort |
|---|---|---|---|
| 1 | Temporal Workflow Engine (cluster + SDK) | 🔴 Critical | Large |
| 2 | Onboarding Workflow Definition | 🔴 Critical | Large |
| 3 | Decision Engine | 🔴 Critical | Medium |
| 4 | Custom Verification Services | 🔴 Critical | Large |
| 5 | Integration Hub (adapter layer) | 🔴 Critical | Medium |
| 6 | Webhook Receiver | 🔴 Critical | Medium |
| 7 | Onboarding API Routes (`/init`, `/submit`) | 🔴 Critical | Medium |
| 8 | PII Vault | 🟡 High | Medium |
| 9 | API Gateway / Load Balancer | 🟡 High | Small |
| 10 | Bank Core Webhook Notifier | 🟡 High | Small |
| 11 | Authentication & Authorization | 🟡 High | Medium |
| 12 | Provider Configuration Model | 🟡 High | Medium |
| 13 | Admin Portal (frontend) | 🟡 High | Large |
| 14 | Mobile SDK (frontend) | 🟡 High | Large |
| 15 | Manual Review Signal flow | 🟡 High | Small |
| 16 | Operational DB / Workflow Logs | 🟢 Medium | Small |

> **Coverage:** ~20% — the Template Service and Tenant Management foundation is built. The entire workflow orchestration, verification, decision, and frontend layers remain.

---

## 5. Recommended Build Order

| Phase | Components | Deliverable |
|---|---|---|
| **Phase 1** | Temporal cluster + Onboarding Workflow + Onboarding API routes | End-to-end happy path with mocked verification |
| **Phase 2** | Decision Engine + Custom Verification Services + Integration Hub + Webhook Receiver | Real verification (internal + third-party) |
| **Phase 3** | PII Vault + Bank Core Notifier + Auth + Provider Config | Production security and bank integration |
| **Phase 4** | Admin Portal + Manual Review + Mobile SDK | Complete user-facing experience |

---

## 6. File Structure

```
onboarding-and-verification-saas/
├── app/
│   ├── main.py                          # FastAPI entrypoint
│   ├── core/
│   │   ├── config.py                    # Settings (env-based)
│   │   └── context.py                   # Tenant ContextVar
│   ├── db/
│   │   ├── session.py                   # Async engine + tenant-scoped sessions
│   │   └── migrations.py               # Schema provisioning + Alembic helpers
│   ├── middleware/
│   │   └── tenants.py                   # X-Tenant-ID header extraction
│   ├── models/
│   │   ├── base.py                      # AuditBase mixin
│   │   ├── shared/tenant.py             # Tenant model (public schema)
│   │   └── tenant_scoped/template.py    # Template + TemplateDefinition models
│   ├── schemas/
│   │   ├── templates/template.py        # API request/response schemas
│   │   ├── templates/form_schema.py     # Form field type definitions
│   │   └── tenants/tenant.py            # Tenant API schemas
│   ├── routes/
│   │   ├── templates/template.py        # Template CRUD endpoints
│   │   └── tenants/tenant.py            # Tenant CRUD endpoints
│   └── services/
│       ├── templates/template.py        # Template business logic
│       └── tenants/tenant.py            # Tenant business logic
├── alembic/                             # Migration scripts
├── docs/
│   └── architecture.md                  # This document
├── Dockerfile
├── docker-compose.dev.yaml
└── pyproject.toml
```
