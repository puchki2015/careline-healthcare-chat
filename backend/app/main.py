"""FastAPI application factory for the Careline backend.

Keeping application construction in ``create_app`` gives tests an isolated app
with test settings while production still exposes the conventional ``app``
object expected by Uvicorn.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a Careline API instance.

    Settings are accepted as an argument so tests do not need to modify the
    developer's real ``.env`` file. In normal operation, settings are loaded
    once from environment variables by ``get_settings``.
    """

    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Startup logging intentionally includes only non-secret operational
        # metadata. Supabase URLs and keys are never included in logs.
        logger.info(
            "application_started",
            extra={
                "environment": application_settings.environment,
                "api_prefix": application_settings.api_v1_prefix,
                "frontend_origin": application_settings.frontend_origin_value,
            },
        )
        yield
        logger.info("application_stopped")

    application = FastAPI(
        title=application_settings.app_name,
        version=application_settings.app_version,
        description="Patient-centric API for Careline.",
        lifespan=lifespan,
    )
    application.state.settings = application_settings
    register_exception_handlers(application)

    # CORS is deliberately restricted to the configured frontend. Wildcard
    # origins must not be used with authenticated browser requests.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[application_settings.frontend_origin_value],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # This middleware is added after CORS so it wraps the full request and logs
    # preflight responses as well as normal API calls.
    application.add_middleware(RequestLoggingMiddleware)

    application.include_router(api_router, prefix=application_settings.api_v1_prefix)

    # Keep the original route for deployment platforms that already probe it.
    # It is excluded from OpenAPI so /api/v1/health remains the documented API.
    application.include_router(health_router, include_in_schema=False)

    return application


app = create_app()
