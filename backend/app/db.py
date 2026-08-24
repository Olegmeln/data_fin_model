"""Подключение к базе данных и инициализация."""
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

logger = logging.getLogger("app.db")


class Base(DeclarativeBase):
    pass


if settings.DATABASE_URL.startswith("sqlite"):
    engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Serverless-окружения (Vercel) долго держат «уснувшие» соединения —
    # pre_ping и recycle переподключаются прозрачно для кода.
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI-зависимость: сессия БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db() -> bool:
    """Лёгкая проверка доступности БД (SELECT 1), схему не меняет."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — недоступность БД не должна ронять импорт приложения
        logger.error("База данных недоступна: %s (%s)", type(exc).__name__, exc)
        return False


def init_db() -> None:
    """Создание таблиц и наполнение справочника статей.

    Идемпотентно: недостающие статьи досоздаются и на существующей базе
    (важно при обновлении версии на Vercel/Postgres).
    """
    from . import models  # noqa: F401  (регистрация моделей)
    from .finmodel.template import DEFAULT_CATEGORIES

    Base.metadata.create_all(engine)
    _stamp_alembic_head()
    with SessionLocal() as db:
        existing = {code for (code,) in db.query(models.Category.code).all()}
        added = False
        for item in DEFAULT_CATEGORIES:
            if item["code"] not in existing:
                db.add(models.Category(code=item["code"], name=item["name"], kind=item["kind"]))
                added = True
        if added:
            db.commit()


def _stamp_alembic_head() -> None:
    """Помечает базу, созданную через create_all, текущей ревизией Alembic.

    Так `alembic upgrade head` на уже работающей базе не будет пытаться
    создать существующие таблицы. Ошибки не критичны: без штампа приложение
    работает, штамп можно поставить вручную (`alembic stamp head`).
    """
    from pathlib import Path

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:  # alembic не установлен (минимальное окружение)
        return
    try:
        ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
        if not ini_path.exists():
            return
        config = Config(str(ini_path))
        config.set_main_option("script_location", str(ini_path.parent / "alembic"))
        command.stamp(config, "head")
    except Exception:  # noqa: BLE001 — штамп не должен ронять запуск приложения
        pass


if __name__ == "__main__":
    # Инициализация новой БД вручную: python -m app.db
    # (создание таблиц, штамп Alembic, сидирование справочника статей).
    init_db()
    print("init_db: ok")
