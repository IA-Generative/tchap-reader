"""FastAPI application factory — multi-tenant version."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.TCHAP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup and shutdown events."""
    missing = settings.validate_config()
    if missing:
        logger.warning("Legacy config incomplete: %s — multi-tenant mode active", missing)
    else:
        logger.info(
            "Tchap Reader starting — homeserver=%s, allowed_rooms=%d",
            settings.TCHAP_HOMESERVER_URL,
            len(settings.allowed_rooms),
        )
    logger.info(
        "Multi-tenant enabled — OpenWebUI: %s, SSO callback: %s",
        settings.OPENWEBUI_BASE_URL,
        settings.SSO_CALLBACK_BASE_URL,
    )
    yield
    logger.info("Tchap Reader shutting down")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Tchap Reader — Matrix Sync & Analysis Service",
        description="Multi-tenant Matrix room analysis for Open WebUI",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
