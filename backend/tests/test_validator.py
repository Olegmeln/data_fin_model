"""Тесты валидатора: внутренняя согласованность и кросс-сверка двух источников."""
from datetime import date

from app.finmodel.assumptions_schema import (
    AssumptionSet, Capex, CapexItem, CreditFacility, Product, ProjectProfile,
    RatePoint, RateSchedule, SourceMethod, SourceRef, Taxes,
)
from app.finmodel.intake.validator import apply_validation, compare, validate


def base_set(**overrides) -> AssumptionSet:
    defaults = dict(
        profile=ProjectProfile(name="Тест", horizon_years=10, model_start=date(2025, 1, 1)),
        products=[Product(name="Продукт", start_price=100)],
        capex=Capex(items=[CapexItem(name="Стройка", amount=1200), CapexItem(name="Сети", amount=150)]),
        financing={"equity_amount": 360, "facilities": [
            CreditFacility(name="Инвестиционный", amount=1440, term_months=72,
                           grace_months=24, rate=RateSchedule.flat(7.1))]},
    )
    defaults.update(overrides)
    return AssumptionSet(**defaults)


class TestValidate:
    def test_clean_set_has_no_issues(self):
        assert validate(base_set()) == []

    def test_no_products_is_blocker(self):
        issues = validate(base_set(products=[]))
        assert any(q.severity == "blocker" and q.field_path == "products" for q in issues)

    def test_missing_price_is_warning(self):
        issues = validate(base_set(products=[Product(name="Без цены")]))
        assert any(q.field_path == "products.0.start_price" for q in issues)

    def test_capex_override_mismatch(self):
        aset = base_set(capex=Capex(
            items=[CapexItem(name="Стройка", amount=1246.6)], total_override=1391.8))
        issues = validate(aset)
        blockers = [q for q in issues if q.field_path == "capex"]
        assert blockers and blockers[0].severity == "blocker"
        assert len(blockers[0].variants) == 2

    def test_underfunding_is_blocker(self):
        aset = base_set(financing={"equity_amount": 100, "facilities": []})
        issues = validate(aset)
        assert any("недофинансирование" in q.question for q in issues)

    def test_grace_not_less_than_term(self):
        aset = base_set(financing={"equity_amount": 360, "facilities": [
            CreditFacility(name="К", amount=1440, term_months=12, grace_months=12,
                           rate=RateSchedule.flat(10))]})
        assert any(q.severity == "blocker" for q in validate(aset)
                   if q.field_path == "financing.facilities.0")

    def test_term_beyond_horizon_is_warning(self):
        aset = base_set(profile=ProjectProfile(name="Т", horizon_years=5))
        issues = validate(aset)
        assert any(q.field_path == "financing.facilities.0.term_months" for q in issues)

    def test_usn_with_vat_flagged(self):
        aset = base_set(taxes=Taxes(regime="УСН 15%", vat=RateSchedule.flat(20)))
        assert any(q.field_path == "taxes.vat" for q in validate(aset))

    def test_rate_out_of_range_is_blocker(self):
        aset = base_set(taxes=Taxes(profit=RateSchedule.flat(250)))
        assert any(q.severity == "blocker" and q.field_path == "taxes.profit"
                   for q in validate(aset))

    def test_low_confidence_asks_confirmation(self):
        aset = base_set(sources={"capex": SourceRef(method=SourceMethod.extracted, confidence=0.4)})
        assert any("низкой уверенностью" in q.question for q in validate(aset))


class TestCompareGreenhouseCase:
    """Синтетическое воспроизведение трёх реальных расхождений обучающей пары."""

    def _plan(self) -> AssumptionSet:  # «бизнес-план»
        return base_set(
            capex=Capex(items=[CapexItem(name="ОС", amount=1391.8),
                               CapexItem(name="Инфраструктура", amount=150)]),
            taxes=Taxes(
                regime="ТОСЭР",
                vat=RateSchedule(points=[RatePoint(value_pct=20),
                                         RatePoint(effective_from=date(2026, 1, 1), value_pct=22)]),
                payroll_contributions=RateSchedule.flat(7.6),
            ),
        )

    def _model(self) -> AssumptionSet:  # «эталонная финмодель»
        return base_set(
            capex=Capex(items=[CapexItem(name="ОС", amount=1246.6),
                               CapexItem(name="Инфраструктура", amount=150)]),
            taxes=Taxes(
                regime="ТОСЭР",
                vat=RateSchedule.flat(20),
                payroll_contributions=RateSchedule.flat(30.4),
            ),
        )

    def test_finds_exactly_three_discrepancies(self):
        issues = compare(self._plan(), self._model(), "бизнес-план", "финмодель")
        paths = sorted(q.field_path for q in issues)
        assert paths == ["capex", "taxes.payroll_contributions", "taxes.vat"]
        assert all(q.severity == "blocker" for q in issues)

    def test_variants_carry_both_sources(self):
        issues = compare(self._plan(), self._model(), "бизнес-план", "финмодель")
        capex_q = next(q for q in issues if q.field_path == "capex")
        assert "бизнес-план" in capex_q.variants[0] and "финмодель" in capex_q.variants[1]

    def test_identical_sets_no_issues(self):
        assert compare(self._plan(), self._plan()) == []


class TestCompareFacilities:
    def test_missing_facility_reported_both_ways(self):
        a = base_set()
        b = base_set(financing={"equity_amount": 360, "facilities": [
            CreditFacility(name="Оборотный", amount=100, term_months=12,
                           rate=RateSchedule.flat(20))]})
        questions = [q.question for q in compare(a, b, "А", "Б")]
        assert any("'Инвестиционный'" in q and "отсутствует в Б" in q for q in questions)
        assert any("'Оборотный'" in q and "отсутствует в А" in q for q in questions)

    def test_amount_mismatch(self):
        a, b = base_set(), base_set()
        b.financing.facilities[0].amount = 1540
        issues = compare(a, b)
        assert any(q.field_path == "financing.facilities.0.amount" for q in issues)


class TestApplyValidation:
    def test_appends_without_duplicates(self):
        aset = base_set(products=[])
        first = apply_validation(aset)
        second = apply_validation(first)
        texts = [q.question for q in second.open_questions]
        assert len(texts) == len(set(texts))
        assert any(q.field_path == "products" for q in second.open_questions)

    def test_original_not_mutated(self):
        aset = base_set(products=[])
        apply_validation(aset)
        assert aset.open_questions == []
