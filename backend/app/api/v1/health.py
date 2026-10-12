"""Lightweight application health endpoint."""

import logging

from fastapi import APIRouter

from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check API health",
    description="Confirms that the FastAPI process is running and able to serve requests.",
)
def health() -> HealthResponse:
    """Return a dependency-free liveness response.

    Database connectivity is intentionally excluded. A liveness probe should
    remain available even when an external dependency is temporarily degraded.
    """

    logger.debug("health_check_succeeded")
    return HealthResponse(status="ok")
