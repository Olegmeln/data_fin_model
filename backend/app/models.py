"""ORM-модели данных."""
from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


class Category(Base):
    """Статья финансовой модели (доход / расход / внутренний перевод)."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    kind = Column(String(16), nullable=False)  # income | expense | transfer


class ImportLog(Base):
    """Журнал загрузок выписок."""

    __tablename__ = "import_logs"

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    fmt = Column(String(16), nullable=False)  # csv | xlsx | 1c
    total_rows = Column(Integer, default=0)
    imported = Column(Integer, default=0)
    duplicates = Column(Integer, default=0)
    needs_review = Column(Integer, default=0)
    status = Column(String(16), default="done")  # done | error
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Operation(Base):
    """Операция движения денежных средств."""

    __tablename__ = "operations"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)  # всегда положительная
    direction = Column(String(4), nullable=False)  # in | out
    counterparty = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)

    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    category = relationship("Category", lazy="joined")
    confidence = Column(Float, nullable=True)
    categorized_by = Column(String(16), nullable=True)  # rule | keyword | ai | user | fallback
    status = Column(String(16), default="needs_review", index=True)  # auto | needs_review | confirmed

    source_hash = Column(String(64), unique=True, index=True)
    import_id = Column(Integer, ForeignKey("import_logs.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Rule(Base):
    """Правило категоризации, выученное на подтверждениях пользователя."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("field", "pattern", name="uq_rule_field_pattern"),)

    id = Column(Integer, primary_key=True)
    field = Column(String(16), nullable=False)  # counterparty | description
    pattern = Column(String(255), nullable=False)  # подстрока в нижнем регистре
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    category = relationship("Category", lazy="joined")
    created_at = Column(DateTime, default=datetime.utcnow)


class PlanValue(Base):
    """Плановое значение по статье на месяц."""

    __tablename__ = "plan_values"
    __table_args__ = (UniqueConstraint("category_id", "month", name="uq_plan_cat_month"),)

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    category = relationship("Category", lazy="joined")
    month = Column(Date, nullable=False)  # первое число месяца
    amount = Column(Numeric(14, 2), nullable=False, default=0)


class BusinessProfile(Base):
    """Профиль бизнеса — результат интерактивного опросника."""

    __tablename__ = "business_profiles"

    id = Column(Integer, primary_key=True)
    industry_code = Column(String(32), nullable=False)
    answers_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Assumption(Base):
    """Допущение: предполагаемое значение статьи на месяц, пока нет факта.

    Жизненный цикл: появился факт за месяц — допущение вытесняется им на
    дашборде и в модели, но сохраняется для истории. Источник: опросник
    (source='survey') или правка пользователя (source='user', приоритетнее).
    """

    __tablename__ = "assumptions"
    __table_args__ = (UniqueConstraint("category_id", "month", name="uq_assumption_cat_month"),)

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    category = relationship("Category", lazy="joined")
    month = Column(Date, nullable=False)  # первое число месяца
    amount = Column(Numeric(14, 2), nullable=False)
    source = Column(String(16), nullable=False, default="user")  # survey | user
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GoogleCredential(Base):
    """OAuth-токены Google для двусторонней синхронизации.

    MVP single-tenant: одна строка на всё приложение (полноценные
    пользователи/рабочие пространства — отдельный пункт дорожной карты).
    """

    __tablename__ = "google_credentials"

    id = Column(Integer, primary_key=True)
    token = Column(Text, nullable=True)            # access token
    refresh_token = Column(Text, nullable=True)    # долгоживущий refresh token
    token_uri = Column(String(255), nullable=True)
    scopes = Column(Text, nullable=True)           # список scope через пробел
    expiry = Column(DateTime, nullable=True)       # срок действия access token (UTC)
    email = Column(String(255), nullable=True)     # почта подключённого аккаунта
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SheetLink(Base):
    """Связанная Google-таблица модели (MVP single-tenant: одна строка)."""

    __tablename__ = "sheet_links"

    id = Column(Integer, primary_key=True)
    spreadsheet_id = Column(String(128), nullable=False)
    url = Column(String(512), nullable=True)
    title = Column(String(255), nullable=True)
    last_pushed_at = Column(DateTime, nullable=True)
    last_pulled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
