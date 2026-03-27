from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app.core.context import tenant_context
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _jsonable(value: Any, *, _depth: int = 0) -> Any:
    """Best-effort conversion to JSON-serializable types.

    This is defensive: validation errors may include raw bytes (request body),
    UUIDs, sets, datetimes, etc. We must never crash while building an error
    response.
    """
    if _depth > 10:
        return str(value)

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return repr(value)

    # Common containers
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, _depth=_depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v, _depth=_depth + 1) for v in sorted(value, key=lambda x: str(x))]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[str(k)] = _jsonable(v, _depth=_depth + 1)
        return out

    # Common scalar-ish types
    try:
        import uuid

        if isinstance(value, uuid.UUID):
            return str(value)
    except Exception:  # noqa: BLE001
        pass

    try:
        from datetime import date, datetime

        if isinstance(value, (datetime, date)):
            return value.isoformat()
    except Exception:  # noqa: BLE001
        pass

    # Pydantic / SQLModel objects
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(), _depth=_depth + 1)
        except Exception:  # noqa: BLE001
            return str(value)

    # Exceptions
    if isinstance(value, Exception):
        return {"type": value.__class__.__name__, "message": str(value)}

    return str(value)


def error_response(
    *,
    status_code: int,
    message: str,
    request: Request | None = None,
    code: str | None = None,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(getattr(request, "state", None), "request_id", None) if request else None
    payload: dict[str, Any] = {
        "error": {
            "code": code or _default_code(status_code),
            "message": message,
        }
    }
    if request_id:
        payload["error"]["request_id"] = request_id
    if details is not None:
        payload["error"]["details"] = _jsonable(details)
    return JSONResponse(payload, status_code=status_code, headers=headers)


def add_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):  # noqa: ANN001
        if isinstance(exc.detail, dict):
            detail = exc.detail
            return error_response(
                status_code=exc.status_code,
                message=str(detail.get("message") or "Request failed"),
                request=request,
                code=detail.get("code"),
                details=detail.get("details"),
                headers=getattr(exc, "headers", None),
            )
        return error_response(
            status_code=exc.status_code,
            message=str(exc.detail) if exc.detail else "Request failed",
            request=request,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):  # noqa: ANN001
        errors = exc.errors()
        hint: str | None = None
        # Common curl mistake: sending JSON string without Content-Type: application/json
        # which makes FastAPI treat the body as a plain string / form.
        if errors:
            try:
                first = errors[0]
                if (
                    isinstance(first, dict)
                    and first.get("type") == "model_attributes_type"
                    and tuple(first.get("loc") or ()) == ("body",)
                ):
                    raw_input = first.get("input")
                    if isinstance(raw_input, str) and raw_input.lstrip().startswith("{"):
                        ctype = (request.headers.get("content-type") or "").lower()
                        if "application/json" not in ctype:
                            hint = "Set header 'Content-Type: application/json' (or use curl --json) when sending JSON bodies."
            except Exception:  # noqa: BLE001
                hint = None

        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Validation error",
            request=request,
            code="validation_error",
            details={"errors": errors, "hint": hint} if hint else errors,
        )

    @app.exception_handler(SQLAlchemyError)
    async def _sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):  # noqa: ANN001
        message = "Database error"
        code = "database_error"
        http_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        debug_details = None

        if get_settings().DEBUG:
            debug_details = {"error": str(exc)[:2000]}

        # Friendly hint for the most common local/dev failure: schema drift.
        # Example: column tenants.tenant_key does not exist
        text = str(exc)
        if "UndefinedColumnError" in text or "does not exist" in text:
            if any(token in text for token in ("schema_name", "tenant_key")) and ("tenants" in text or "public.tenants" in text):
                message = "Database schema is out of date. Run migrations (python -m alembic upgrade head)."
                code = "database_schema_outdated"

        # Tenant schema not provisioned / drifted.
        if "UndefinedTableError" in text and "does not exist" in text:
            tenant = tenant_context.get()
            if tenant:
                message = "Tenant database schema is not provisioned. Run tenant migrations for this tenant."
                code = "tenant_schema_outdated"
                http_status = status.HTTP_503_SERVICE_UNAVAILABLE

        logger.exception(
            "Database error (request_id=%s, path=%s): %s",
            getattr(request.state, "request_id", None),
            request.url.path,
            exc,
        )
        return error_response(
            status_code=http_status,
            message=message,
            request=request,
            code=code,
            details=(
                {
                    "tenant": tenant_context.get(),
                    "hint": (
                        f"Run: alembic -x tenant_schema=tenant_{tenant_context.get()} upgrade head"
                        if tenant_context.get() and tenant_context.get().isidentifier()
                        else "Run the tenant schema migrations for this tenant."
                    ),
                }
                if code == "tenant_schema_outdated"
                else debug_details
            ),
        )

    @app.exception_handler(ExceptionGroup)  # type: ignore[name-defined]
    async def _exception_group_handler(request: Request, exc: ExceptionGroup):  # noqa: ANN001
        logger.exception(
            "Unhandled exception group (request_id=%s, path=%s)",
            getattr(request.state, "request_id", None),
            request.url.path,
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            request=request,
            code="internal_error",
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ANN001
        logger.exception(
            "Unhandled error (request_id=%s, path=%s): %s",
            getattr(request.state, "request_id", None),
            request.url.path,
            exc,
        )
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            request=request,
            code="internal_error",
        )


def _default_code(status_code: int) -> str:
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "unauthorized"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "forbidden"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        return "validation_error"
    if status_code >= 500:
        return "internal_error"
    return "bad_request"
