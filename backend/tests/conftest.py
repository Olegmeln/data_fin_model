"""Общие фикстуры тестов.

Важно: DATABASE_URL задаётся ДО импорта приложения, потому что engine
создаётся на этапе импорта ``app.db``.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP_DIR = tempfile.mkdtemp(prefix="finmodel_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ.pop("ANTHROPIC_API_KEY", None)  # тесты не ходят в ИИ

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

REPO_ROOT = BACKEND_DIR.parent
SAMPLES_DIR = REPO_ROOT / "samples"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """TestClient с полностью инициализированным приложением (init_db)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session():
    """Сессия БД с гарантированно созданной схемой и справочником статей."""
    from app.db import SessionLocal, init_db

    init_db()
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(scope="session")
def sample_csv() -> bytes:
    return (SAMPLES_DIR / "выписка_demo_июнь_2026.csv").read_bytes()


@pytest.fixture(scope="session")
def sample_1c() -> bytes:
    return (SAMPLES_DIR / "statement_1c_demo.txt").read_bytes()
