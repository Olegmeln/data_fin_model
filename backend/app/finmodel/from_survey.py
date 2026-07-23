"""Мост «опросник → assumptions.v1».

Профиль бизнеса, собранный опросником (BusinessProfile.answers), конвертируется
в набор допущений публичной схемы. Это первый шаг слияния двух движков экспорта:
и legacy-экспорт, и книга по sheet registry должны питаться одним слоем допущений.

Каждый заполненный раздел получает источник method=derived («из опросника»):
память предпочтений его не перекрывает, а форма показывает происхождение.
"""
from __future__ import annotations

from .assumptions_schema import (
    AssumptionSet,
    Capex,
    CreditFacility,
    Opex,
    OpexItem,
    Product,
    ProductKind,
    ProjectProfile,
    RateSchedule,
    Scenario,
    SourceMethod,
    SourceRef,
    Taxes,
    Valuation,
)
from .industries import get_industry

# налог на прибыль по режиму: приближение для моста (уточняется в форме допущений)
_PROFIT_RATE_BY_MODE = {
    "usn6": 6.0,
    "usn15": 15.0,
    "npd": 6.0,
    "osno": 25.0,
}

_TAX_MODE_NAMES = {
    "usn6": "УСН «Доходы» 6%",
    "usn15": "УСН «Доходы минус расходы» 15%",
    "npd": "НПД / патент",
    "osno": "ОСНО",
    "unknown": None,
}


def _num(answers: dict, key: str) -> float | None:
    value = answers.get(key)
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def assumption_set_from_survey(answers: dict, name: str = "Проект из опросника") -> AssumptionSet:
    """Собирает AssumptionSet из ответов опросника (без обращения к БД)."""
    derived = SourceRef(method=SourceMethod.derived, note="из опросника")
    sources: dict[str, SourceRef] = {}

    industry = get_industry(str(answers.get("industry") or ""))
    horizon_months = _num(answers, "planning_horizon") or 12
    profile = ProjectProfile(
        name=name,
        industry=industry["name"] if industry else None,
        project_type="запуск" if answers.get("business_age") == "new" else "действующий бизнес",
        horizon_years=max(1, round(horizon_months / 12)),
    )
    sources["profile"] = derived

    products: list[Product] = []
    revenue = _num(answers, "monthly_revenue")
    if revenue:
        products.append(Product(
            name="Выручка (базовый план)",
            kind=ProductKind.service,
            unit="мес",
            start_price=revenue,
            start_volume=1,
            ramp_up_months=4 if answers.get("business_age") == "new" else 0,
        ))
        sources["products"] = derived

    opex_items: list[OpexItem] = []
    payroll = _num(answers, "payroll_monthly")
    if payroll:
        opex_items.append(OpexItem(name="ФОТ", monthly_amount=payroll))
    rent = _num(answers, "rent_monthly")
    if rent:
        opex_items.append(OpexItem(name="Аренда и коммунальные", monthly_amount=rent))
    if opex_items:
        sources["opex"] = derived

    capex_total = _num(answers, "capex_total")
    capex = Capex(total_override=capex_total, depreciation_months=60) if capex_total else Capex()
    if capex_total:
        sources["capex"] = derived

    facilities: list[CreditFacility] = []
    loan = _num(answers, "loan_amount")
    if loan and answers.get("funding_source") in ("loan", "mixed", None, ""):
        facilities.append(CreditFacility(
            name="Кредит (опросник)",
            amount=loan,
            term_months=int(horizon_months),
            rate=RateSchedule.flat(_num(answers, "loan_rate") or 17.0),
        ))
    loan_used = loan if facilities else 0.0
    equity = max(0.0, (capex_total or 0.0) - (loan_used or 0.0)) if capex_total else 0.0
    financing = {"equity_amount": equity, "facilities": facilities}
    if facilities or equity:
        sources["financing"] = derived

    tax_mode = str(answers.get("tax_mode") or "unknown")
    taxes = Taxes(regime=_TAX_MODE_NAMES.get(tax_mode))
    if tax_mode in _PROFIT_RATE_BY_MODE:
        taxes = Taxes(regime=_TAX_MODE_NAMES.get(tax_mode),
                      profit=RateSchedule.flat(_PROFIT_RATE_BY_MODE[tax_mode]))
        sources["taxes"] = derived

    discount = _num(answers, "discount_rate")
    valuation = Valuation(discount_rate_pct=discount if discount else 9.0)
    if discount:
        sources["valuation"] = derived

    scenarios = [Scenario(name="Базовый")]
    if revenue:
        scenarios = [
            Scenario(name="Пессимистичный", overrides={"products.0.start_price": revenue * 0.8}),
            Scenario(name="Базовый"),
            Scenario(name="Оптимистичный", overrides={"products.0.start_price": revenue * 1.15}),
        ]

    return AssumptionSet(
        profile=profile,
        products=products,
        capex=capex,
        opex=Opex(items=opex_items),
        financing=financing,
        taxes=taxes,
        valuation=valuation,
        scenarios=scenarios,
        sources=sources,
    )
