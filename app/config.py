"""Central configuration via Pydantic Settings."""

from __future__ import annotations

import logging

from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # Legacy single-bot config (kept for backward compat)
    TCHAP_HOMESERVER_URL: str = "https://matrix.agent.tchap.gouv.fr"
    TCHAP_ACCESS_TOKEN: str = ""
    TCHAP_USER_ID: str = ""
    TCHAP_DEVICE_ID: str = "OWUI_BOT"
    TCHAP_STORE_PATH: str = "/app/data/tchap.db"
    TCHAP_ALLOWED_ROOM_IDS: str = ""
    TCHAP_DEFAULT_WINDOW_HOURS: int = 168
    TCHAP_API_RATE_LIMIT_PER_SEC: float = 1.0
    TCHAP_MAX_MESSAGES_PER_ANALYSIS: int = 1000
    TCHAP_ANONYMIZE_OUTPUT: bool = True
    TCHAP_LOG_LEVEL: str = "INFO"
    TCHAP_MAX_WINDOW_DAYS: int = 30

    # Multi-tenant config
    OPENWEBUI_BASE_URL: str = "http://open-webui:8080"
    SSO_CALLBACK_BASE_URL: str = "http://tchapreader:8087"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def allowed_rooms(self) -> set[str]:
        return {r.strip() for r in self.TCHAP_ALLOWED_ROOM_IDS.split(",") if r.strip()}

    def validate_config(self) -> list[str]:
        missing: list[str] = []
        if not self.TCHAP_ACCESS_TOKEN:
            missing.append("TCHAP_ACCESS_TOKEN")
        if not self.TCHAP_USER_ID:
            missing.append("TCHAP_USER_ID")
        if not self.TCHAP_ALLOWED_ROOM_IDS:
            missing.append("TCHAP_ALLOWED_ROOM_IDS")
        return missing


settings = Settings()
