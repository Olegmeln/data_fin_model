"""Подключение к базе данных и инициализация."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI-зависимость: сессия БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Создание таблиц и наполнение справочника статей при первом запуске."""
    from . import models  # noqa: F401  (регистрация моделей)
    from .finmodel.template import DEFAULT_CATEGORIES

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.query(models.Category).first() is None:
            for item in DEFAULT_CATEGORIES:
                db.add(models.Category(code=item["code"], name=item["name"], kind=item["kind"]))
            db.commit()
