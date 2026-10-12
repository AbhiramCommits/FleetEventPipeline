"""Application settings loaded from environment variables with safe local defaults.

Everything can be overridden through the process environment or a ``.env`` file
(see ``.env.example``). The defaults target the local Docker Compose stack so a
fresh clone runs without any configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    db_dsn: str
    fleet_api_base: str
    fleet_api_token: str
    eventgrid_endpoint: str
    eventgrid_key: str
    handler_url: str
    page_size: int
    lookback_hours: int
    alert_webhook: str
    repo_root: Path

    @property
    def eventgrid_emulated(self) -> bool:
        """True when no real Event Grid topic is configured."""
        return not self.eventgrid_endpoint


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings(
        db_dsn=_get("DB_DSN", "postgresql://fleet:fleet@localhost:55432/fleet"),
        fleet_api_base=_get("FLEET_API_BASE", "http://localhost:8099").rstrip("/"),
        fleet_api_token=_get("FLEET_API_TOKEN", "local-dev-token"),
        eventgrid_endpoint=_get("EVENTGRID_ENDPOINT", "").rstrip("/"),
        eventgrid_key=_get("EVENTGRID_KEY", ""),
        handler_url=_get("HANDLER_URL", "http://localhost:7099/api/eventgrid"),
        page_size=int(_get("PAGE_SIZE", "1000")),
        lookback_hours=int(_get("LOOKBACK_HOURS", "720")),
        alert_webhook=_get("ALERT_WEBHOOK", ""),
        repo_root=REPO_ROOT,
    )
