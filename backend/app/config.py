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

    @property
    def db_kind(self) -> str:
        return "postgres" if self.DATABASE_URL.startswith("postgresql") else "sqlite"

    @property
    def db_persistent(self) -> bool:
        """На Vercel SQLite живёт в /tmp и обнуляется — данные не сохраняются."""
        return not (os.getenv("VERCEL") and self.db_kind == "sqlite")

    # --- Слой LLM (необязателен: без него работают движки правил) -----------
    # Провайдер: anthropic | openai | none. Пусто — автоопределение по ключам.
    # «openai» покрывает любой OpenAI-совместимый сервис (OpenRouter, DeepSeek,
    # Together, Groq, Mistral) и локальные Ollama/LM Studio/vLLM через LLM_BASE_URL.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "").strip()
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "").strip()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()

    # Обратная совместимость: прежние переменные продолжают работать.
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    ANTHROPIC_API_URL: str = os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")

    @property
    def llm_model(self) -> str:
        """Модель активного провайдера (LLM_MODEL приоритетнее)."""
        if self.LLM_MODEL:
            return self.LLM_MODEL
        provider = (self.LLM_PROVIDER or ("anthropic" if self.ANTHROPIC_API_KEY else "openai")).lower()
        return self.ANTHROPIC_MODEL if provider == "anthropic" else "gpt-4o-mini"

    @property
    def llm_base_url(self) -> str:
        return self.LLM_BASE_URL

    # Порог уверенности: ниже него операция получает статус «требует подтверждения».
    CONFIDENCE_THRESHOLD: float = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.8"))

    # Лимиты загрузки файлов (защита памяти и времени ответа, особенно на Vercel).
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_MB", "15")) * 1024 * 1024
    MAX_STATEMENT_ROWS: int = int(os.getenv("MAX_STATEMENT_ROWS", "20000"))
    MAX_INTAKE_FILES: int = int(os.getenv("MAX_INTAKE_FILES", "10"))

    @property
    def ai_enabled(self) -> bool:
        """ИИ доступен, если задан любой ключ или локальный base_url."""
        provider = (self.LLM_PROVIDER or "").lower()
        if provider == "none":
            return False
        if provider == "anthropic":
            return bool(self.ANTHROPIC_API_KEY)
        if provider == "openai":
            return bool(self.LLM_API_KEY or self.LLM_BASE_URL)
        return bool(self.ANTHROPIC_API_KEY or self.LLM_API_KEY)

    @property
    def cors_origins(self) -> list[str]:
        """Разрешённые CORS-origin (CORS_ORIGINS, через запятую). По умолчанию `*` — MVP/демо;
        перед выходом в продакшен задайте домен фронтенда."""
        raw = os.getenv("CORS_ORIGINS", "*")
        origins = [item.strip() for item in raw.split(",") if item.strip()]
        return origins or ["*"]

    @property
    def db_auto_init(self) -> bool:
        """Создавать ли схему БД при старте приложения.

        По умолчанию: включено локально (удобство разработки), выключено на
        Vercel — в продакшене схема управляется миграциями (alembic upgrade head),
        cold start не должен менять БД. Переопределяется DB_AUTO_INIT=1/0.
        """
        value = os.getenv("DB_AUTO_INIT")
        if value is not None:
            return value.strip().lower() not in ("0", "false", "no", "")
        return not os.getenv("VERCEL")


settings = Settings()
