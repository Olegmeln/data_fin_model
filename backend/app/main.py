"""Точка входа приложения.

Запуск для разработки:
    uvicorn app.main:app --reload --port 8000
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import router
from .db import init_db

app = FastAPI(
    title="data_fin_model API",
    description="Живая финансовая модель: выписки → ИИ-категоризация → дашборд",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP: при выходе в продакшен ограничить доменом фронтенда
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
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
