"""Схема допущений финмодели — `finmodel.assumptions.v1`.

Это будущий публичный контракт AFM&C (часть стандарта), поэтому:
- имена полей стабильны и осмысленны;
- каждый факт может нести источник и уверенность (карта `sources`);
- ставки задаются расписаниями во времени, а не константами;
- schema-версия зашита в корне документа.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_ID = "finmodel.assumptions.v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- источники

class SourceMethod(str, Enum):
    extracted = "extracted"   # извлечено агентом из документа
    user = "user"             # введено/подтверждено пользователем
    default = "default"       # профиль по умолчанию
    derived = "derived"       # вычислено из других полей


class SourceRef(Strict):
    """Происхождение факта: документ/страница, метод, уверенность 0..1."""

    method: SourceMethod
    document: str | None = None       # имя файла или идентификатор документа
    locator: str | None = None        # страница/лист/ячейка/раздел
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    note: str | None = None


# ------------------------------------------------------------- расписания

class RatePoint(Strict):
    """Точка расписания: значение действует с даты (None = с начала модели)."""

    effective_from: date | None = None
    value_pct: float


class RateSchedule(Strict):
    """Ставка как расписание во времени (НДС 20% → 22% с 2026-01-01 и т.п.)."""

    points: list[RatePoint] = Field(min_length=1)

    @model_validator(mode="after")
    def _sorted_and_single_start(self) -> "RateSchedule":
        starts = [p for p in self.points if p.effective_from is None]
        if len(starts) > 1:
            raise ValueError("в расписании может быть только одна стартовая точка (effective_from=None)")
        dated = [p.effective_from for p in self.points if p.effective_from is not None]
        if dated != sorted(dated):
            raise ValueError("точки расписания должны идти по возрастанию даты")
        return self

    @classmethod
    def flat(cls, value_pct: float) -> "RateSchedule":
        return cls(points=[RatePoint(value_pct=value_pct)])

    def value_at(self, on: date) -> float:
        current: float | None = None
        for p in self.points:
            if p.effective_from is None or p.effective_from <= on:
                current = p.value_pct
            else:
                break
        if current is None:
            raise ValueError(f"расписание не определено на {on}: нет стартовой точки")
        return current


# ---------------------------------------------------------------- разделы

class ProjectProfile(Strict):
    name: str
    project_type: str | None = None       # строительство→производство→реализация и т.п.
    industry: str | None = None
    location: str | None = None
    currency: str = "RUB"
    horizon_years: int = Field(5, ge=1, le=50)
    model_start: date | None = None


class ProductKind(str, Enum):
    goods = "goods"
    service = "service"


class Product(Strict):
    name: str
    kind: ProductKind = ProductKind.goods
    unit: str | None = None                       # кг, шт, час...
    start_price: float | None = Field(None, ge=0)  # цена за единицу без НДС
    price_indexation: str | None = None            # напр. "ИПЦ Минэкономразвития"
    ramp_up_months: int | None = Field(None, ge=0)
    extra: dict[str, Any] = Field(default_factory=dict)  # отраслевые параметры (урожайность и т.п.)


class CapexItem(Strict):
    name: str
    amount: float = Field(ge=0)
    vat_included: bool = False
    schedule_months: tuple[int, int] | None = None  # (месяц начала, месяц конца) от старта модели


class Capex(Strict):
    items: list[CapexItem] = Field(default_factory=list)
    total_override: float | None = Field(None, ge=0)  # если итог задан документом без разбивки
    depreciation_months: int | None = Field(None, ge=1)

    @property
    def total(self) -> float:
        if self.total_override is not None:
            return self.total_override
        return sum(i.amount for i in self.items)


class FacilityKind(str, Enum):
    investment = "investment"
    working_capital = "working_capital"


class CreditFacility(Strict):
    name: str
    kind: FacilityKind = FacilityKind.investment
    amount: float = Field(ge=0)
    term_months: int = Field(ge=1)
    rate: RateSchedule
    grace_months: int = Field(0, ge=0)           # льготный период до начала погашения тела
    rate_formula: str | None = None              # напр. "ключевая 17% × 70% + 2 п.п."


class Financing(Strict):
    equity_amount: float = Field(0, ge=0)
    facilities: list[CreditFacility] = Field(default_factory=list)

    @property
    def debt_amount(self) -> float:
        return sum(f.amount for f in self.facilities)

    @property
    def equity_share_pct(self) -> float | None:
        total = self.equity_amount + self.debt_amount
        return self.equity_amount / total * 100 if total else None


class Taxes(Strict):
    regime: str | None = None                    # ОСНО / УСН / ТОСЭР ...
    profit: RateSchedule = Field(default_factory=lambda: RateSchedule.flat(25))
    vat: RateSchedule = Field(default_factory=lambda: RateSchedule.flat(20))
    property_pct: float = Field(2.2, ge=0)
    land_pct: float = Field(0, ge=0)
    payroll_contributions: RateSchedule = Field(default_factory=lambda: RateSchedule.flat(30.4))


class Valuation(Strict):
    discount_rate_pct: float = Field(9.0, ge=0)
    reinvest_roa_pct: float | None = None        # для MIRR (И-4)


class Scenario(Strict):
    """Сценарий = именованный набор переопределений (путь → значение)."""

    name: str
    overrides: dict[str, Any] = Field(default_factory=dict)  # "taxes.vat" → расписание и т.п.


class OpenQuestion(Strict):
    """Пункт «требует уточнения» — результат работы валидатора/аудита."""

    question: str
    field_path: str | None = None
    variants: list[str] = Field(default_factory=list)
    severity: str = "warning"                    # info | warning | blocker


class AssumptionSet(Strict):
    """Корневой документ допущений проекта."""

    schema_id: str = Field(SCHEMA_ID, alias="schema")
    profile: ProjectProfile
    products: list[Product] = Field(default_factory=list)
    capex: Capex = Field(default_factory=Capex)
    financing: Financing = Field(default_factory=Financing)
    taxes: Taxes = Field(default_factory=Taxes)
    valuation: Valuation = Field(default_factory=Valuation)
    scenarios: list[Scenario] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    sources: dict[str, SourceRef] = Field(default_factory=dict)  # json-path → источник

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _checks(self) -> "AssumptionSet":
        if self.schema_id != SCHEMA_ID:
            raise ValueError(f"неподдерживаемая версия схемы: {self.schema_id!r}, ожидается {SCHEMA_ID!r}")
        names = [p.name for p in self.products]
        if len(names) != len(set(names)):
            raise ValueError("имена продуктов должны быть уникальны")
        for path in self.sources:
            if not path or path.startswith("."):
                raise ValueError(f"некорректный путь в sources: {path!r}")
        return self

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True, indent=2)

    @classmethod
    def from_json(cls, raw: str | bytes | dict) -> "AssumptionSet":
        if isinstance(raw, dict):
            return cls.model_validate(raw)
        return cls.model_validate_json(raw)


def export_json_schema() -> dict:
    """JSON Schema контракта — публикуемая часть стандарта AFM&C."""
    return AssumptionSet.model_json_schema(by_alias=True)


if __name__ == "__main__":  # python -m app.finmodel.assumptions_schema > schema.json
    import json
    print(json.dumps(export_json_schema(), ensure_ascii=False, indent=2))
