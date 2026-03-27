"""Template Service — FastAPI application entrypoint.

This service implements schema-based multi-tenancy with:
- Public schema: Tenant registry + Baseline templates (system-owned)
- Per-tenant schemas: Tenant-specific templates and data

Key features:
- BaselineTemplates are immutable by tenants (view-only)
- TenantTemplates can extend baselines with customizations
- Schema isolation via PostgreSQL search_path
"""

import logging
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.auth import JWTAuthMiddleware, shutdown_jwks_refresh, startup_jwks_refresh
from app.core.config import get_settings
from app.core.errors import add_exception_handlers
from app.db.session import dispose_engines
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.tenants import TenantMiddleware

logger = logging.getLogger(__name__)

_CONSTRAINT_RE = re.compile(r'unique constraint "([^"]+)"')

# ── Routers ──────────────────────────────────────────────────────────
from app.routes.tenants import tenant_router
from app.routes.transforms import transform_router
from app.routes.baseline_templates import baseline_template_router
from app.routes.tenant_templates import tenant_template_router
from app.routes.submissions import submission_router
from app.routes.products import product_router
from app.routes.auth import auth_router
from app.routes.authz import authz_router
import argparse

settings = get_settings()
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown hook.

    On startup  → initialize auth background tasks.
    On shutdown → dispose any initialized DB connection pools gracefully.
    """
    await startup_jwks_refresh()

    # if settings.TEMPORAL_ENABLED:
    #     try:
    #         await temporal_client.connect()
    #     except Exception as exc:
    #         if settings.TEMPORAL_REQUIRED:
    #             raise
    #         logger.warning(
    #             "Temporal connection failed; continuing without Temporal "
    #             "(error: %s). Set TEMPORAL_REQUIRED=true to fail fast.",
    #             exc,
    #         )
    yield
    await shutdown_jwks_refresh()
    await dispose_engines()


# ── App ──────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
    description="""
    Multi-tenant Template Service with schema-based isolation.
    
    ## Features
    - **Baseline Templates**: System-defined templates that tenants can view and extend
    - **Tenant Templates**: Tenant-specific templates with customizations (KYC configurations)
    - **Products**: Tenant-owned onboarding products linking KYC templates with lifecycle management
    - **Submissions**: Form submissions against templates with full workflow support
    - **Schema Isolation**: Each tenant has their own PostgreSQL schema
    
    ## Authentication
    - Pass `X-Tenant-ID` header for tenant-scoped operations
    - Admin routes (baseline template mutations) require admin auth (not implemented)
    """,
)

# ── Global exception handlers ─────────────────────────────────────────

_CONSTRAINT_MESSAGES: dict[str, str] = {
    # Tenant schema — question groups / questions
    "uq_qgroup_version_key": "A question group with this unique_key already exists in the version.",
    "uq_question_group_key": "A question with this unique_key already exists in the group.",
    # Public schema — baseline question groups / questions
    "uq_baseline_qgroup_version_key": "A baseline question group with this unique_key already exists in the version.",
    "uq_baseline_question_group_key": "A baseline question with this unique_key already exists in the group.",
    # Submission answers
    "uq_answer_submission_question": "An answer for this question already exists in the submission.",
    # Tenants
    "tenants_schema_name_key": "A tenant with this schema_name already exists.",
    "ix_tenants_schema_name": "A tenant with this schema_name already exists.",
    # Baseline templates
    "uq_baseline_template_type_level": "A baseline template with this template_type and level already exists.",
    # Products
    "products_product_code_key": "A product with this product_code already exists.",
    "ix_products_product_code": "A product with this product_code already exists.",
}


def _extract_constraint_name(exc: IntegrityError) -> str | None:
    """Best-effort extraction of the violated constraint name from PG/asyncpg."""
    orig = getattr(exc, "orig", None)
    if orig is not None:
        for attr in ("constraint_name", "constraint"):
            name = getattr(orig, attr, None)
            if name:
                return name
    diag = getattr(orig, "diag", None) if orig else None
    if diag:
        name = getattr(diag, "constraint_name", None)
        if name:
            return name
    msg = str(exc)
    match = _CONSTRAINT_RE.search(msg)
    if match:
        return match.group(1)
    return None


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request, exc: IntegrityError):
    constraint = _extract_constraint_name(exc)
    detail = _CONSTRAINT_MESSAGES.get(constraint, "") if constraint else ""
    if not detail:
        detail = "A record with the same unique value already exists."
        if constraint:
            detail += f" (constraint: {constraint})"
    logger.warning("IntegrityError [%s]: %s", constraint or "unknown", detail)
    return JSONResponse(status_code=409, content={"detail": detail})


# Error handlers (consistent prod-friendly JSON)
add_exception_handlers(app)

# Register middleware (last added runs first)
app.add_middleware(TenantMiddleware)
app.add_middleware(JWTAuthMiddleware)
app.add_middleware(RequestIdMiddleware)

# Register routers
# Auth proxy routes (Kong → this service → Keycloak)
app.include_router(auth_router)

# Public routes (no X-Tenant-ID required)
app.include_router(tenant_router, prefix=settings.API_V1_PREFIX)
app.include_router(baseline_template_router, prefix=settings.API_V1_PREFIX)
app.include_router(authz_router, prefix=settings.API_V1_PREFIX)

# Tenant-scoped routes (require X-Tenant-ID header)
app.include_router(tenant_template_router, prefix=settings.API_V1_PREFIX)
app.include_router(submission_router, prefix=settings.API_V1_PREFIX)
app.include_router(product_router, prefix=settings.API_V1_PREFIX)
app.include_router(transform_router, prefix=settings.API_V1_PREFIX)


# ── Health check ─────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/", tags=["ops"])
async def root() -> dict:
    """Root endpoint with API info."""
    return {
        "service": settings.APP_NAME,
        "version": "2.0.0",
        "architecture": "schema-based-multi-tenancy",
        "docs": "/docs",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.main")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI app with uvicorn")
    serve.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "7090")))
    serve.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("RELOAD", "").lower() in {"1", "true", "yes"},
        help="Enable auto-reload (best when not debugging).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(list(argv) if argv is not None else os.sys.argv[1:])
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
