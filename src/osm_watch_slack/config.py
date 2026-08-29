from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    database_path: str = "data/watches.db"
    state_path: str = "data/state.txt"
    overpass_base_url: str = "https://overpass-api.de"
    replication_state_url: str = (
        "https://planet.openstreetmap.org/replication/minute/state.txt"
    )
    log_level: str = "INFO"
    user_watch_cap: int = 20
    channel_watch_cap: int = 50
    digest_threshold: int = 5

    @classmethod
    def from_env(cls) -> Config:
        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token:
            raise RuntimeError("SLACK_BOT_TOKEN environment variable is required")
        if not app_token:
            raise RuntimeError("SLACK_APP_TOKEN environment variable is required")
        return cls(
            slack_bot_token=bot_token,
            slack_app_token=app_token,
            database_path=os.environ.get("DATABASE_PATH", cls.database_path),
            state_path=os.environ.get("STATE_PATH", cls.state_path),
            overpass_base_url=os.environ.get("OVERPASS_BASE_URL", cls.overpass_base_url),
            replication_state_url=os.environ.get(
                "REPLICATION_STATE_URL", cls.replication_state_url
            ),
            log_level=os.environ.get("LOG_LEVEL", cls.log_level),
            user_watch_cap=int(os.environ.get("USER_WATCH_CAP", cls.user_watch_cap)),
            channel_watch_cap=int(
                os.environ.get("CHANNEL_WATCH_CAP", cls.channel_watch_cap)
            ),
            digest_threshold=int(
                os.environ.get("DIGEST_THRESHOLD", cls.digest_threshold)
            ),
        )
