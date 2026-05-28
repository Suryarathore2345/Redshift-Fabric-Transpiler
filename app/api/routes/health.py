"""Health check endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.settings import settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    app_name: str


@router.get("/health", response_model=HealthResponse, summary="Health check")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        app_name=settings.app_name,
    )
