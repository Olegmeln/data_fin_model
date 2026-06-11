"""Конфигурация приложения. Значения берутся из переменных окружения и файла .env."""
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Минималистичная загрузка .env (без внешних зависимостей)."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
            break


_load_dotenv()


def _database_url() -> str:
    """Определение строки подключения к БД.

    Порядок: DATABASE_URL → POSTGRES_URL (так переменную называет интеграция
    Vercel/Neon) → локальный SQLite. На Vercel файловая система доступна
    только для чтения, поэтому запасной SQLite живёт в /tmp (данные
    эфемерны — для постоянного хранения подключите Postgres).
    """
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
    if not url:
        return "sqlite:////tmp/finmodel.db" if os.getenv("VERCEL") else "sqlite:///./finmodel.db"
    # SQLAlchemy 2 не принимает устаревшую схему postgres:// (её выдают Neon/Heroku).
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Settings:
    """Настройки сервиса."""

    DATABASE_URL: str = _database_url()

    # ИИ-категоризация (необязательно). Без ключа работает движок правил.
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    ANTHROPIC_API_URL: str = os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")

    # Порог уверенности: ниже него операция получает статус «требует подтверждения».
    CONFIDENCE_THRESHOLD: float = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


settings = Settings()
