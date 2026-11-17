"""Versioned API routes."""

from fastapi import APIRouter

from .health import router as health_router
from .projects import router as projects_router
from .transcriptions import router as transcriptions_router
from .exports import router as exports_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(transcriptions_router)
api_router.include_router(exports_router)

__all__ = ["api_router"]
