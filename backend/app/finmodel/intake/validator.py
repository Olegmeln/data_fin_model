"""Валидатор допущений — пункт 3 критического пути.

Два режима:
- `validate(aset)` — внутренняя согласованность одного набора
  (арифметика, полнота, здравые диапазоны);
- `compare(a, b)` — кросс-сверка двух наборов, извлечённых из разных
  источников (бизнес-план ↔ финмодель, пакет документов ↔ требования).

Оба возвращают список OpenQuestion; ничего не «чинят» молча — решение
всегда за пользователем. Это же ядро будущего Audit-агента (И-1):
для аудита внешней модели compare() получает набор, извлечённый из неё.
"""
from __future__ import annotations

from datetime import date

from ..assumptions_schema import AssumptionSet, OpenQuestion, RateSchedule

REL_TOLERANCE = 0.01  # 1% — расхождения меньше считаем шумом округления


def _close(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b))
    return scale == 0 or abs(a - b) / scale <= REL_TOLERANCE


def _q(question: str, path: str | None = None, severity: str = "warning",
       variants: list[str] | None = None) -> OpenQuestion:
    return OpenQuestion(question=question, field_path=path, severity=severity,
                        variants=variants or [])


# ------------------------------------------------------------ внутренние проверки

def validate(aset: AssumptionSet) -> list[OpenQuestion]:
    """Проверки согласованности одного набора допущений."""
    issues: list[OpenQuestion] = []
    issues += _check_products(aset)
    issues += _check_capex(aset)
    issues += _check_financing(aset)
    issues += _check_taxes(aset)
    issues += _check_valuation(aset)
    issues += _check_confidence(aset)
    return issues


def _check_products(aset: AssumptionSet) -> list[OpenQuestion]:
    issues = []
    if not aset.products:
        issues.append(_q("Не задан ни один продукт — модели не из чего формировать выручку",
                         "products", severity="blocker"))
    for i, product in enumerate(aset.products):
        if product.start_price is None:
            issues.append(_q(f"Для продукта {product.name!r} не задана стартовая цена",
                             f"products.{i}.start_price"))
    return issues


def _check_capex(aset: AssumptionSet) -> list[OpenQuestion]:
    issues = []
    capex = aset.capex
    if capex.total_override is not None and capex.items:
        items_sum = sum(i.amount for i in capex.items)
        if not _close(items_sum, capex.total_override):
            issues.append(_q(
                f"Итог CAPEX ({capex.total_override:g}) не сходится с суммой статей ({items_sum:g})",
                "capex", severity="blocker",
                variants=[f"принять итог {capex.total_override:g}", f"принять сумму статей {items_sum:g}"],
            ))
    horizon_months = aset.profile.horizon_years * 12
    if capex.depreciation_months and capex.depreciation_months > horizon_months * 3:
        issues.append(_q(
            f"Срок амортизации ({capex.depreciation_months} мес) выглядит нереалистично "
            f"для горизонта {aset.profile.horizon_years} лет",
            "capex.depreciation_months"))
    return issues


def _check_financing(aset: AssumptionSet) -> list[OpenQuestion]:
    issues = []
    financing = aset.financing
    total_funding = financing.equity_amount + financing.debt_amount
    capex_total = aset.capex.total
    if capex_total and total_funding and total_funding < capex_total * (1 - REL_TOLERANCE):
        issues.append(_q(
            f"Финансирование ({total_funding:g}) не покрывает CAPEX ({capex_total:g}) — недофинансирование",
            "financing", severity="blocker"))
    horizon_months = aset.profile.horizon_years * 12
    for i, facility in enumerate(financing.facilities):
        if facility.grace_months >= facility.term_months:
            issues.append(_q(
                f"У кредита {facility.name!r} льготный период ({facility.grace_months} мес) "
                f"не меньше срока ({facility.term_months} мес)",
                f"financing.facilities.{i}", severity="blocker"))
        if facility.term_months > horizon_months:
            issues.append(_q(
                f"Срок кредита {facility.name!r} ({facility.term_months} мес) выходит за горизонт "
                f"модели ({horizon_months} мес) — хвост долга останется за кадром",
                f"financing.facilities.{i}.term_months"))
    return issues


def _check_taxes(aset: AssumptionSet) -> list[OpenQuestion]:
    issues = []
    regime = (aset.taxes.regime or "").upper()
    on = aset.profile.model_start or date.today()
    if "УСН" in regime and aset.taxes.vat.value_at(on) > 0:
        issues.append(_q(
            f"Режим {aset.taxes.regime!r} обычно не предполагает НДС, "
            f"но ставка НДС задана {aset.taxes.vat.value_at(on):g}%",
            "taxes.vat"))
    for name, schedule in (("profit", aset.taxes.profit), ("vat", aset.taxes.vat),
                           ("payroll_contributions", aset.taxes.payroll_contributions)):
        for point in schedule.points:
            if not 0 <= point.value_pct <= 100:
                issues.append(_q(
                    f"Ставка taxes.{name} = {point.value_pct:g}% вне диапазона 0–100",
                    f"taxes.{name}", severity="blocker"))
    return issues


def _check_valuation(aset: AssumptionSet) -> list[OpenQuestion]:
    rate = aset.valuation.discount_rate_pct
    if rate > 60:
        return [_q(f"Ставка дисконтирования {rate:g}% выглядит ошибкой ввода",
                   "valuation.discount_rate_pct", severity="blocker")]
    return []


def _check_confidence(aset: AssumptionSet, threshold: float = 0.6) -> list[OpenQuestion]:
    issues = []
    for path, source in aset.sources.items():
        if source.confidence < threshold:
            issues.append(_q(
                f"Поле {path} извлечено с низкой уверенностью ({source.confidence:.2f}) — подтвердите значение",
                path, severity="warning"))
    return issues


# ------------------------------------------------------------ кросс-сверка

def compare(a: AssumptionSet, b: AssumptionSet,
            label_a: str = "источник А", label_b: str = "источник Б") -> list[OpenQuestion]:
    """Сверка двух наборов допущений; расхождения → вопросы с вариантами."""
    issues: list[OpenQuestion] = []

    def diff_num(path: str, va: float | None, vb: float | None, what: str) -> None:
        if va is None or vb is None:
            return
        if not _close(float(va), float(vb)):
            issues.append(_q(
                f"{what}: {label_a} — {va:g}, {label_b} — {vb:g}",
                path, severity="blocker",
                variants=[f"{label_a}: {va:g}", f"{label_b}: {vb:g}"]))

    diff_num("profile.horizon_years", a.profile.horizon_years, b.profile.horizon_years, "Горизонт, лет")
    diff_num("capex", a.capex.total or None, b.capex.total or None, "Итог CAPEX")
    diff_num("financing.equity_amount", a.financing.equity_amount, b.financing.equity_amount,
             "Собственные средства")
    diff_num("financing", a.financing.debt_amount, b.financing.debt_amount, "Заёмные средства")
    diff_num("valuation.discount_rate_pct", a.valuation.discount_rate_pct,
             b.valuation.discount_rate_pct, "Ставка дисконтирования")

    _diff_schedule(issues, "taxes.vat", a.taxes.vat, b.taxes.vat, "Ставка НДС", label_a, label_b)
    _diff_schedule(issues, "taxes.profit", a.taxes.profit, b.taxes.profit,
                   "Налог на прибыль", label_a, label_b)
    _diff_schedule(issues, "taxes.payroll_contributions", a.taxes.payroll_contributions,
                   b.taxes.payroll_contributions, "Взносы с ФОТ", label_a, label_b)

    facilities_b = {f.name: f for f in b.financing.facilities}
    for i, fa in enumerate(a.financing.facilities):
        fb = facilities_b.get(fa.name)
        if fb is None:
            issues.append(_q(f"Кредит {fa.name!r} есть в {label_a}, но отсутствует в {label_b}",
                             f"financing.facilities.{i}"))
            continue
        diff_num(f"financing.facilities.{i}.amount", fa.amount, fb.amount, f"Кредит {fa.name!r}: сумма")
        diff_num(f"financing.facilities.{i}.term_months", fa.term_months, fb.term_months,
                 f"Кредит {fa.name!r}: срок, мес")
    for fb_name in facilities_b:
        if fb_name not in {f.name for f in a.financing.facilities}:
            issues.append(_q(f"Кредит {fb_name!r} есть в {label_b}, но отсутствует в {label_a}",
                             "financing.facilities"))
    return issues


def _diff_schedule(issues: list[OpenQuestion], path: str, sa: RateSchedule, sb: RateSchedule,
                   what: str, label_a: str, label_b: str) -> None:
    """Сравнение расписаний по значениям во всех точках перелома обоих."""
    probes: set[date] = set()
    for schedule in (sa, sb):
        for point in schedule.points:
            probes.add(point.effective_from or date(1900, 1, 1))
    for on in sorted(probes):
        try:
            va, vb = sa.value_at(on), sb.value_at(on)
        except ValueError:
            issues.append(_q(f"{what}: расписания определены с разных дат", path))
            return
        if not _close(va, vb):
            issues.append(_q(
                f"{what} c {on.isoformat() if on.year > 1900 else 'старта'}: "
                f"{label_a} — {va:g}%, {label_b} — {vb:g}%",
                path, severity="blocker",
                variants=[f"{label_a}: {va:g}%", f"{label_b}: {vb:g}%"]))
            return  # одного вопроса на расписание достаточно


# ------------------------------------------------------------ применение

def apply_validation(aset: AssumptionSet, extra: list[OpenQuestion] | None = None) -> AssumptionSet:
    """Возвращает копию набора с дополненными open_questions (без дублей по тексту)."""
    seen = {q.question for q in aset.open_questions}
    merged = list(aset.open_questions)
    for question in validate(aset) + (extra or []):
        if question.question not in seen:
            merged.append(question)
            seen.add(question.question)
    return aset.model_copy(update={"open_questions": merged})
