"""Движок книги — пункт 5 критического пути.

Считает помесячные ряды из AssumptionSet. Сценарий — параметр расчёта
(переопределения по путям), а не копия листа (И-3). MIRR корректируется
на доходность реинвестирования ROA (И-4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..assumptions_schema import AssumptionSet, Scenario


class BookError(Exception):
    """Ошибка построения книги, понятная пользователю."""


# ------------------------------------------------------------- сценарии

def apply_overrides(aset: AssumptionSet, overrides: dict) -> AssumptionSet:
    """Возвращает копию набора с применёнными переопределениями путь→значение."""
    if not overrides:
        return aset
    data = aset.model_dump(by_alias=True, exclude_none=True, mode="json")
    for path, value in overrides.items():
        node = data
        parts = path.split(".")
        for key in parts[:-1]:
            if isinstance(node, list):
                node = node[int(key)]
            else:
                node = node.setdefault(key, {})
        last = parts[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    try:
        return AssumptionSet.from_json(data)
    except Exception as exc:
        raise BookError(f"сценарные переопределения ломают схему: {exc}") from exc


def resolve_scenario(aset: AssumptionSet, scenario_name: str | None) -> AssumptionSet:
    if scenario_name is None:
        return aset
    for scenario in aset.scenarios:
        if scenario.name == scenario_name:
            return apply_overrides(aset, scenario.overrides)
    known = ", ".join(s.name for s in aset.scenarios) or "—"
    raise BookError(f"сценарий {scenario_name!r} не найден (есть: {known})")


# ------------------------------------------------------------- ряды

def _add_months(start: date, count: int) -> date:
    month_index = start.month - 1 + count
    return date(start.year + month_index // 12, month_index % 12 + 1, 1)


@dataclass
class BookData:
    """Результат расчёта: помесячные ряды и метрики."""

    months: list[date]
    revenue: list[float]
    revenue_by_product: dict[str, list[float]]
    opex: list[float]
    ebitda: list[float]
    capex: list[float]
    debt_draw: list[float]
    interest: list[float]
    principal: list[float]
    debt_outstanding: list[float]
    net_cf: list[float]
    cumulative_cf: list[float]
    opex_by_item: dict[str, list[float]] = field(default_factory=dict)
    depreciation: list[float] = field(default_factory=list)
    ebit: list[float] = field(default_factory=list)
    tax: list[float] = field(default_factory=list)
    net_income: list[float] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def build_book(aset: AssumptionSet, scenario: str | None = None) -> BookData:
    aset = resolve_scenario(aset, scenario)
    horizon = aset.profile.horizon_years * 12
    start = aset.profile.model_start or date.today().replace(day=1)
    months = [_add_months(start, i) for i in range(horizon)]

    revenue_by_product: dict[str, list[float]] = {}
    for product in aset.products:
        price = product.start_price or 0.0
        volume_full = product.start_volume or 0.0
        ramp = product.ramp_up_months or 0
        series = []
        for i in range(horizon):
            share = 1.0 if ramp == 0 else min(1.0, (i + 1) / ramp)
            series.append(price * volume_full * share)
        revenue_by_product[product.name] = series
    revenue = [sum(values) for values in zip(*revenue_by_product.values())] if revenue_by_product else [0.0] * horizon

    opex_by_item: dict[str, list[float]] = {}
    for item in aset.opex.items:
        name = item.name
        while name in opex_by_item:  # дубликаты имён не теряем
            name += " ·"
        opex_by_item[name] = [
            item.monthly_amount + revenue[i] * item.pct_of_revenue / 100 for i in range(horizon)
        ]
    opex = ([sum(values) for values in zip(*opex_by_item.values())]
            if opex_by_item else [0.0] * horizon)

    ebitda = [revenue[i] - opex[i] for i in range(horizon)]

    capex = [0.0] * horizon
    for item in aset.capex.items:
        first, last = item.schedule_months or (0, 0)
        first, last = max(0, first), min(horizon - 1, max(first, last))
        span = last - first + 1
        for i in range(first, last + 1):
            capex[i] += item.amount / span
    if not aset.capex.items and aset.capex.total_override:
        capex[0] = aset.capex.total_override

    # амортизация: линейная, позиция начинает амортизироваться со следующего
    # месяца после завершения графика ввода (или с месяца 1 для итога без разбивки)
    depreciation = [0.0] * horizon
    dep_months = aset.capex.depreciation_months
    if dep_months:
        starts: list[tuple[int, float]] = []
        for item in aset.capex.items:
            first, last = item.schedule_months or (0, 0)
            starts.append((min(horizon - 1, max(first, last)) + 1, item.amount))
        if not aset.capex.items and aset.capex.total_override:
            starts.append((1, aset.capex.total_override))
        for start_month, amount in starts:
            monthly = amount / dep_months
            for i in range(start_month, min(horizon, start_month + dep_months)):
                depreciation[i] += monthly

    debt_draw = [0.0] * horizon
    interest = [0.0] * horizon
    principal = [0.0] * horizon
    debt_outstanding = [0.0] * horizon
    for facility in aset.financing.facilities:
        outstanding = 0.0
        repay_months = max(1, facility.term_months - facility.grace_months)
        monthly_principal = facility.amount / repay_months
        for i in range(horizon):
            if i == 0:
                debt_draw[i] += facility.amount
                outstanding += facility.amount
            rate_pct = facility.rate.value_at(months[i])
            interest[i] += outstanding * rate_pct / 100 / 12
            if facility.grace_months <= i < facility.term_months and outstanding > 0:
                payment = min(monthly_principal, outstanding)
                principal[i] += payment
                outstanding -= payment
            debt_outstanding[i] += outstanding

    # P&L: EBIT, налог на прибыль (ставка — расписание во времени), чистая прибыль
    ebit = [ebitda[i] - depreciation[i] for i in range(horizon)]
    tax = [0.0] * horizon
    net_income = [0.0] * horizon
    for i in range(horizon):
        taxable = ebit[i] - interest[i]
        rate_pct = aset.taxes.profit.value_at(months[i])
        tax[i] = max(0.0, taxable * rate_pct / 100)
        net_income[i] = taxable - tax[i]

    # чистый поток — после налога на прибыль
    net_cf = [
        ebitda[i] - tax[i] - capex[i] + debt_draw[i] - interest[i] - principal[i]
        for i in range(horizon)
    ]
    cumulative = []
    running = 0.0
    for value in net_cf:
        running += value
        cumulative.append(running)

    book = BookData(
        months=months, revenue=revenue, revenue_by_product=revenue_by_product,
        opex=opex, ebitda=ebitda, capex=capex, debt_draw=debt_draw,
        interest=interest, principal=principal, debt_outstanding=debt_outstanding,
        net_cf=net_cf, cumulative_cf=cumulative,
        opex_by_item=opex_by_item, depreciation=depreciation,
        ebit=ebit, tax=tax, net_income=net_income,
    )
    book.metrics = _metrics(aset, book)
    return book


# ------------------------------------------------------------- метрики

def _npv(monthly_rate: float, flows: list[float]) -> float:
    return sum(cf / (1 + monthly_rate) ** (i + 1) for i, cf in enumerate(flows))


def _irr_monthly(flows: list[float]) -> float | None:
    if not any(f < 0 for f in flows) or not any(f > 0 for f in flows):
        return None
    low, high = -0.99, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        if _npv(mid, flows) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _mirr_yearly(flows: list[float], finance_rate_pct: float, reinvest_rate_pct: float) -> float | None:
    """MIRR: оттоки дисконтируются по ставке привлечения, притоки
    реинвестируются по ROA (И-4). Возвращает годовой процент."""
    n = len(flows)
    fin_m = finance_rate_pct / 100 / 12
    reinv_m = reinvest_rate_pct / 100 / 12
    pv_out = sum(-cf / (1 + fin_m) ** i for i, cf in enumerate(flows) if cf < 0)
    fv_in = sum(cf * (1 + reinv_m) ** (n - 1 - i) for i, cf in enumerate(flows) if cf > 0)
    if pv_out <= 0 or fv_in <= 0:
        return None
    mirr_monthly = (fv_in / pv_out) ** (1 / (n - 1)) - 1
    return ((1 + mirr_monthly) ** 12 - 1) * 100


def _metrics(aset: AssumptionSet, book: BookData) -> dict:
    # проектные потоки: без кредитных движений, после налога (FCFF-подход)
    project_flows = [
        book.ebitda[i] - book.tax[i] - book.capex[i] for i in range(len(book.months))
    ]
    monthly_discount = (1 + aset.valuation.discount_rate_pct / 100) ** (1 / 12) - 1
    npv = _npv(monthly_discount, project_flows)
    irr_m = _irr_monthly(project_flows)
    irr = ((1 + irr_m) ** 12 - 1) * 100 if irr_m is not None else None
    reinvest = aset.valuation.reinvest_roa_pct
    mirr = (_mirr_yearly(project_flows, aset.valuation.discount_rate_pct, reinvest)
            if reinvest is not None else None)

    payback = None
    running = 0.0
    for i, cf in enumerate(project_flows):
        running += cf
        if running >= 0 and any(f < 0 for f in project_flows[: i + 1]):
            payback = i + 1
            break

    dscr_min = None
    debt_service = [book.interest[i] + book.principal[i] for i in range(len(book.months))]
    years = len(book.months) // 12
    dscr_by_year = []
    for y in range(years):
        service = sum(debt_service[y * 12:(y + 1) * 12])
        cash = sum(book.ebitda[y * 12:(y + 1) * 12])
        if service > 0:
            dscr_by_year.append(round(cash / service, 2))
    if dscr_by_year:
        dscr_min = min(dscr_by_year)

    return {
        "npv": round(npv, 2),
        "irr_pct": round(irr, 2) if irr is not None else None,
        "mirr_pct": round(mirr, 2) if mirr is not None else None,
        "payback_months": payback,
        "discount_rate_pct": aset.valuation.discount_rate_pct,
        "dscr_by_year": dscr_by_year,
        "dscr_min": dscr_min,
        "revenue_total": round(sum(book.revenue), 2),
        "capex_total": round(sum(book.capex), 2),
    }
