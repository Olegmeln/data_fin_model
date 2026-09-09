"""Тесты миграций Alembic: схема из миграций совпадает со схемой моделей."""
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from conftest import BACKEND_DIR


def _alembic_config(db_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _fresh_db_url() -> str:
    path = Path(tempfile.mkdtemp(prefix="alembic_test_")) / "m.db"
    return f"sqlite:///{path}"


def test_upgrade_head_creates_full_schema(monkeypatch):
    url = _fresh_db_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", url)

    command.upgrade(_alembic_config(url), "head")

    from app.db import Base

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    expected = set(Base.metadata.tables.keys())
    assert expected <= tables, f"после миграций нет таблиц: {expected - tables}"
    assert "alembic_version" in tables


def test_downgrade_base_removes_schema(monkeypatch):
    url = _fresh_db_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("app.config.settings.DATABASE_URL", url)

    config = _alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert tables <= {"alembic_version"}, f"после downgrade остались таблицы: {tables}"


def test_init_db_stamps_head():
    """База, созданная приложением через create_all, помечена головной ревизией."""
    from sqlalchemy import text

    from app.db import engine, init_db

    init_db()
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version, "init_db не поставил штамп alembic_version"
