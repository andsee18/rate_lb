import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    group_chat_id: int
    admin_ids: frozenset[int]
    database_path: str
    webapp_origins: list[str]
    public_api_url: str


def load_settings() -> Settings:
    admin_ids = frozenset(
        int(item.strip())
        for item in os.getenv("ADMIN_IDS", "").split(",")
        if item.strip()
    )
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS must contain at least one Telegram user ID")

    return Settings(
        bot_token=required_env("BOT_TOKEN"),
        group_chat_id=int(required_env("GROUP_CHAT_ID")),
        admin_ids=admin_ids,
        database_path=os.getenv("DATABASE_PATH", "rate_lb.sqlite3"),
        public_api_url=os.getenv(
            "PUBLIC_API_URL",
            "https://rate-lb-backend.onrender.com",
        ).rstrip("/"),
        webapp_origins=[
            item.strip()
            for item in os.getenv("WEBAPP_ORIGINS", "*").split(",")
            if item.strip()
        ],
    )
