"""Точка входа приложения.

Запуск для разработки:
    uvicorn app.main:app --reload --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import router
from .config import settings
from .db import check_db, init_db

app = FastAPI(
    title="data_fin_model API",
    description="Живая финансовая модель: выписки → ИИ-категоризация → дашборд",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # CORS_ORIGINS в env; по умолчанию * (MVP)
    allow_methods=["*"],
    allow_headers=["*"],
)

# Локально: автосоздание схемы и сидирование для удобства разработки.
# В продакшене (Vercel: DB_AUTO_INIT выключен по умолчанию) старт приложения
# схему не меняет — миграции применяются отдельно: alembic upgrade head.
if settings.db_auto_init:
    init_db()
else:
    check_db()

app.include_router(router, prefix="/api")

_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Демо-интерфейс (один HTML-файл, без сборки)."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/legacy", include_in_schema=False)
def legacy() -> FileResponse:
    """Прежний демо-интерфейс (сохранён на время перехода на новый UX)."""
    return FileResponse(_STATIC_DIR / "legacy.html")
