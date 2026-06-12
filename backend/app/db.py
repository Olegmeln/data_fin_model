"""Подключение к базе данных и инициализация."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


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


def init_db() -> None:
    """Создание таблиц и наполнение справочника статей.

    Идемпотентно: недостающие статьи досоздаются и на существующей базе
    (важно при обновлении версии на Vercel/Postgres).
    """
    from . import models  # noqa: F401  (регистрация моделей)
    from .finmodel.template import DEFAULT_CATEGORIES

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        existing = {code for (code,) in db.query(models.Category.code).all()}
        added = False
        for item in DEFAULT_CATEGORIES:
            if item["code"] not in existing:
                db.add(models.Category(code=item["code"], name=item["name"], kind=item["kind"]))
                added = True
        if added:
            db.commit()
