"""Тесты движка книги: ряды, кредит, метрики (MIRR/DSCR), сценарии как параметр."""
from datetime import date

import pytest

from app.finmodel.assumptions_schema import (
    AssumptionSet, Capex, CapexItem, CreditFacility, Opex, OpexItem, Product,
    ProjectProfile, RatePoint, RateSchedule, Scenario, Valuation,
)
from app.finmodel.book import BookError, apply_overrides, build_book


def demo_set(**overrides) -> AssumptionSet:
    defaults = dict(
        profile=ProjectProfile(name="Демо", horizon_years=3, model_start=date(2026, 1, 1)),
        products=[Product(name="Товар", start_price=10, start_volume=100, ramp_up_months=4)],
        opex=Opex(items=[OpexItem(name="Аренда", monthly_amount=200),
                         OpexItem(name="Логистика", pct_of_revenue=10)]),
        capex=Capex(items=[CapexItem(name="Оборудование", amount=1200, schedule_months=(0, 3))]),
        financing={"equity_amount": 300, "facilities": [
            CreditFacility(name="Кредит", amount=900, term_months=24, grace_months=6,
                           rate=RateSchedule.flat(12))]},
        valuation=Valuation(discount_rate_pct=12, reinvest_roa_pct=20),
    )
    defaults.update(overrides)
    return AssumptionSet(**defaults)


class TestSeries:
    def test_revenue_ramp_linear(self):
        book = build_book(demo_set())
        # разгон 4 мес: 25%, 50%, 75%, 100% от 10*100=1000
        assert book.revenue[:5] == [250.0, 500.0, 750.0, 1000.0, 1000.0]

    def test_opex_fixed_plus_variable(self):
        book = build_book(demo_set())
        # месяц 4: 200 фикс + 10% от 1000
        assert book.opex[4] == pytest.approx(300.0)
        assert book.ebitda[4] == pytest.approx(700.0)

    def test_capex_spread_over_schedule(self):
        book = build_book(demo_set())
        assert book.capex[:4] == [300.0] * 4
        assert sum(book.capex) == pytest.approx(1200.0)

    def test_capex_total_override_hits_month_zero(self):
        aset = demo_set(capex=Capex(total_override=500))
        book = build_book(aset)
        assert book.capex[0] == 500.0
        assert sum(book.capex) == 500.0

    def test_horizon_length(self):
        book = build_book(demo_set())
        assert len(book.months) == 36
        assert book.months[0] == date(2026, 1, 1)
        assert book.months[-1] == date(2028, 12, 1)


class TestDepreciationAndPL:
    def test_no_depreciation_without_term(self):
        book = build_book(demo_set())
        assert all(v == 0 for v in book.depreciation)

    def test_linear_depreciation_starts_after_schedule(self):
        aset = demo_set(capex=Capex(
            items=[CapexItem(name="Оборудование", amount=1200, schedule_months=(0, 3))],
            depreciation_months=12))
        book = build_book(aset)
        assert book.depreciation[3] == 0.0            # ещё строим
        assert book.depreciation[4] == pytest.approx(100.0)   # 1200/12 со следующего месяца
        assert book.depreciation[15] == pytest.approx(100.0)
        assert book.depreciation[16] == 0.0
        assert sum(book.depreciation) == pytest.approx(1200.0)

    def test_total_override_depreciates_from_month_one(self):
        aset = demo_set(capex=Capex(total_override=600, depreciation_months=6))
        book = build_book(aset)
        assert book.depreciation[0] == 0.0
        assert book.depreciation[1] == pytest.approx(100.0)
        assert sum(book.depreciation) == pytest.approx(600.0)

    def test_opex_by_item_sums_to_opex(self):
        book = build_book(demo_set())
        assert set(book.opex_by_item) == {"Аренда", "Логистика"}
        for i in (0, 4, 20):
            assert sum(s[i] for s in book.opex_by_item.values()) == pytest.approx(book.opex[i])

    def test_profit_tax_and_net_income(self):
        aset = demo_set(capex=Capex(
            items=[CapexItem(name="О", amount=1200, schedule_months=(0, 3))],
            depreciation_months=12))
        book = build_book(aset)
        i = 10  # рабочий месяц: выручка 1000, opex 300, амортизация 100
        ebit = book.ebitda[i] - book.depreciation[i]
        assert book.ebit[i] == pytest.approx(ebit)
        taxable = ebit - book.interest[i]
        assert book.tax[i] == pytest.approx(max(0, taxable * 0.25))  # ставка по умолчанию 25%
        assert book.net_income[i] == pytest.approx(taxable - book.tax[i])

    def test_net_cf_is_after_tax(self):
        book = build_book(demo_set())
        i = 10  # прибыльный месяц
        expected = (book.ebitda[i] - book.tax[i] - book.capex[i]
                    + book.debt_draw[i] - book.interest[i] - book.principal[i])
        assert book.tax[i] > 0
        assert book.net_cf[i] == pytest.approx(expected)

    def test_no_tax_on_losses(self):
        aset = demo_set(products=[Product(name="Товар", start_price=1, start_volume=10)])
        book = build_book(aset)
        assert all(t == 0 for t in book.tax)
        assert book.net_income[0] < 0


class TestStaffAndCovenants:
    def _staffed(self, **kw):
        from app.finmodel.assumptions_schema import Covenants, Milestone, Staff, StaffRole
        return demo_set(
            staff=Staff(roles=[
                StaffRole(name="Директор", monthly_salary=100),
                StaffRole(name="Рабочие", count=3, monthly_salary=50, start_month=2),
            ]),
            milestones=[Milestone(name="Старт стройки", month=0, kind="capex"),
                        Milestone(name="Запуск", month=4, kind="launch")],
            **kw,
        )

    def test_payroll_series_respects_start_month(self):
        book = build_book(self._staffed())
        assert book.payroll_by_role["Директор"][0] == 100
        assert book.payroll_by_role["Рабочие"][1] == 0
        assert book.payroll_by_role["Рабочие"][2] == 150

    def test_payroll_and_contributions_flow_into_opex(self):
        book = build_book(self._staffed())
        # месяц 4: аренда 200 + логистика 100 + ФОТ 250 + взносы 250*30.4%
        assert "ФОТ (штат)" in book.opex_by_item
        assert book.opex_by_item["Страховые взносы"][4] == pytest.approx(250 * 0.304)
        assert book.opex[4] == pytest.approx(200 + 100 + 250 + 76)

    def test_contributions_included_skips_extra(self):
        from app.finmodel.assumptions_schema import Staff, StaffRole
        aset = demo_set(staff=Staff(contributions_included=True,
                                    roles=[StaffRole(name="Все", monthly_salary=100)]))
        book = build_book(aset)
        assert "Страховые взносы" not in book.opex_by_item

    def test_covenant_metrics_reported(self):
        metrics = build_book(demo_set()).metrics
        assert metrics["icr_by_year"], "ICR должен считаться при наличии процентов"
        assert metrics["net_debt_to_ebitda_by_year"]
        assert metrics["covenant_breaches"] == []  # пороги не заданы

    def test_balance_identity_holds_monthly(self):
        aset = demo_set(capex=Capex(
            items=[CapexItem(name="О", amount=1200, schedule_months=(0, 3))],
            depreciation_months=12))
        book = build_book(aset)
        for i in range(len(book.months)):
            assets = book.fixed_assets[i] + book.cash[i]
            liabilities = book.equity_book[i] + book.debt_outstanding[i]
            assert assets == pytest.approx(liabilities), f"месяц {i}"

    def test_cash_starts_with_equity(self):
        book = build_book(demo_set())
        # месяц 0: equity 300 + чистый поток месяца
        assert book.cash[0] == pytest.approx(300 + book.net_cf[0])

    def test_covenant_breach_becomes_open_question(self):
        from app.finmodel.assumptions_schema import Covenants, CreditFacility, RateSchedule
        from app.finmodel.intake.validator import validate
        aset = demo_set(financing={
            "equity_amount": 300,
            "facilities": [CreditFacility(name="Кредит", amount=900, term_months=24,
                                          grace_months=6, rate=RateSchedule.flat(12))],
            "covenants": Covenants(dscr_min=99.0)})
        questions = validate(aset)
        assert any("Ковенант не выполняется" in q.question for q in questions)

    def test_no_covenant_questions_without_thresholds(self):
        from app.finmodel.intake.validator import validate
        questions = validate(demo_set())
        assert not any("Ковенант" in q.question for q in questions)

    def test_covenant_breach_detected(self):
        from app.finmodel.assumptions_schema import Covenants, CreditFacility, RateSchedule
        aset = demo_set(financing={
            "equity_amount": 300,
            "facilities": [CreditFacility(name="Кредит", amount=900, term_months=24,
                                          grace_months=6, rate=RateSchedule.flat(12))],
            "covenants": Covenants(dscr_min=99.0)})
        metrics = build_book(aset).metrics
        assert any("DSCR" in b for b in metrics["covenant_breaches"])


class TestCredit:
    def test_draw_interest_and_grace(self):
        book = build_book(demo_set())
        assert book.debt_draw[0] == 900.0
        # весь грейс тело не гасится, проценты на полный долг: 900*12%/12=9
        assert book.principal[:6] == [0.0] * 6
        assert book.interest[0] == pytest.approx(9.0)
        assert book.interest[5] == pytest.approx(9.0)

    def test_linear_repayment_after_grace(self):
        book = build_book(demo_set())
        # 18 платежей по 50 после 6 мес грейса
        assert book.principal[6] == pytest.approx(50.0)
        assert book.principal[23] == pytest.approx(50.0)
        assert book.principal[24] == 0.0
        assert book.debt_outstanding[23] == pytest.approx(0.0)
        assert sum(book.principal) == pytest.approx(900.0)

    def test_interest_declines_with_repayment(self):
        book = build_book(demo_set())
        assert book.interest[7] < book.interest[6] < book.interest[5] + 1e-9

    def test_rate_schedule_applies_by_date(self):
        aset = demo_set(financing={"equity_amount": 300, "facilities": [
            CreditFacility(name="К", amount=1200, term_months=36, grace_months=36 - 1,
                           rate=RateSchedule(points=[
                               RatePoint(value_pct=12),
                               RatePoint(effective_from=date(2027, 1, 1), value_pct=24)]))]})
        book = build_book(aset)
        assert book.interest[11] == pytest.approx(1200 * 0.12 / 12)
        assert book.interest[12] == pytest.approx(1200 * 0.24 / 12)


class TestMetrics:
    def test_npv_positive_for_profitable_project(self):
        metrics = build_book(demo_set()).metrics
        assert metrics["npv"] > 0
        assert metrics["irr_pct"] is not None and metrics["irr_pct"] > 12

    def test_mirr_uses_reinvest_roa(self):
        low = demo_set(valuation=Valuation(discount_rate_pct=12, reinvest_roa_pct=5))
        high = demo_set(valuation=Valuation(discount_rate_pct=12, reinvest_roa_pct=40))
        assert build_book(high).metrics["mirr_pct"] > build_book(low).metrics["mirr_pct"]

    def test_mirr_none_without_roa(self):
        aset = demo_set(valuation=Valuation(discount_rate_pct=12))
        assert build_book(aset).metrics["mirr_pct"] is None

    def test_payback_detected(self):
        metrics = build_book(demo_set()).metrics
        assert metrics["payback_months"] is not None
        assert 1 < metrics["payback_months"] <= 36

    def test_dscr_reported_and_min(self):
        metrics = build_book(demo_set()).metrics
        assert len(metrics["dscr_by_year"]) == 2  # долг живёт 2 года
        assert metrics["dscr_min"] == min(metrics["dscr_by_year"])


class TestScenarios:
    def test_override_changes_result(self):
        base = build_book(demo_set()).metrics["npv"]
        stressed = apply_overrides(demo_set(), {"products.0.start_price": 7})
        assert build_book(stressed).metrics["npv"] < base

    def test_named_scenario_as_parameter(self):
        aset = demo_set(scenarios=[Scenario(name="Пессимистичный",
                                            overrides={"products.0.start_volume": 60})])
        base = build_book(aset).metrics["revenue_total"]
        pess = build_book(aset, scenario="Пессимистичный").metrics["revenue_total"]
        assert pess == pytest.approx(base * 0.6)

    def test_unknown_scenario_raises(self):
        with pytest.raises(BookError, match="не найден"):
            build_book(demo_set(), scenario="Нет такого")

    def test_broken_override_raises(self):
        with pytest.raises(BookError, match="ломают схему"):
            apply_overrides(demo_set(), {"products.0.start_price": -5})

    def test_base_set_not_mutated_by_scenario(self):
        aset = demo_set(scenarios=[Scenario(name="X", overrides={"products.0.start_price": 1})])
        build_book(aset, scenario="X")
        assert aset.products[0].start_price == 10
