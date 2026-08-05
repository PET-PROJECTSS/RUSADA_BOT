import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None and raw.strip() else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_dir: Path = Path(__file__).resolve().parent.parent

    bot_token: str = os.getenv("BOT_TOKEN", "")
    allowed_users: list[int] = field(
        default_factory=lambda: [
            int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip().isdigit()
        ]
    )

    rusada_url: str = os.getenv("RUSADA_URL", "https://course.rusada.ru")
    rusada_email: str = os.getenv("RUSADA_EMAIL", "")
    rusada_password: str = os.getenv("RUSADA_PASSWORD", "")
    default_course_id: int = _get_int("DEFAULT_COURSE_ID", 53)

    headless: bool = _get_bool("HEADLESS", True)
    viewport_width: int = _get_int("VIEWPORT_WIDTH", 1280)
    viewport_height: int = _get_int("VIEWPORT_HEIGHT", 720)
    default_timeout_ms: int = _get_int("DEFAULT_TIMEOUT_MS", 15000)

    db_host: str = os.getenv("RUSADA_DB_HOST", "")
    db_name: str = os.getenv("RUSADA_DB_NAME", "")
    db_user: str = os.getenv("RUSADA_DB_USER", "")
    db_password: str = os.getenv("RUSADA_DB_PASSWORD", "")
    db_sslmode: str = os.getenv("RUSADA_DB_SSLMODE", "require")

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    logs_dir: Path = Path(os.getenv("LOGS_DIR", "logs"))
    screenshots_dir: Path = Path(os.getenv("SCREENSHOTS_DIR", "screenshots"))

    health_port: int = _get_int("HEALTH_PORT", 8000)

    @property
    def db_configured(self) -> bool:
        return bool(self.db_host and self.db_name and self.db_user and self.db_password)

    def validate(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.rusada_email:
            missing.append("RUSADA_EMAIL")
        if not self.rusada_password:
            missing.append("RUSADA_PASSWORD")
        if missing:
            raise RuntimeError("Missing required env vars: " + ", ".join(missing))
