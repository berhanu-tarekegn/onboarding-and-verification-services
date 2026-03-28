from fastapi import APIRouter

from app.routes.auth.routes import router as auth_proxy_router
from app.routes.auth.device import router as auth_device_router

auth_router = APIRouter()
auth_router.include_router(auth_proxy_router)
auth_router.include_router(auth_device_router)

__all__ = ["auth_router"]
