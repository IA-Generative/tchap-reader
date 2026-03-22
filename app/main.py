"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api import router
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.TCHAP_LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Tchap Reader — Matrix Sync & Analysis Service",
        version="0.1.0",
    )
    application.include_router(router)

    @application.on_event("startup")
    async def startup():
        missing = settings.validate_config()
        if missing:
            logger.error("Missing config: %s", missing)
        else:
            logger.info("Tchap Reader starting — homeserver=%s, allowed_rooms=%d",
                        settings.TCHAP_HOMESERVER_URL, len(settings.allowed_rooms))

    return application


app = create_app()
