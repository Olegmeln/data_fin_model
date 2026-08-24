"""Тесты инвестиционных метрик (NPV / IRR / окупаемость) в builder."""
import pytest

from app.finmodel.builder import _invest_metrics, _npv


def test_npv_zero_rate_is_sum():
    flows = [-100.0, 60.0, 60.0]
    assert _npv(0.0, flows) == pytest.approx(20.0)


def test_npv_discounts_future_flows():
    flows = [-100.0, 110.0]
    assert _npv(0.10, flows) == pytest.approx(-100 / 1.1 + 110 / 1.1**2)


def test_metrics_empty_flows():
    result = _invest_metrics([], 9.0)
    assert result["npv"] is None
    assert result["irr_pct"] is None
    assert result["payback_months"] is None
    assert result["discount_rate_pct"] == 9.0


def test_metrics_classic_project():
    # Инвестиция 1000, затем 12 месяцев по 150: проект окупается и IRR определён.
    flows = [-1000.0] + [150.0] * 12
    result = _invest_metrics(flows, 9.0)
    assert result["npv"] is not None
    assert result["npv"] > 0
    assert result["irr_pct"] is not None
    assert result["irr_pct"] > 9.0  # доходность выше ставки дисконтирования
    # Кумулятив: -1000, затем +150 в месяц → выходит в плюс на 8-й приток (индекс 7 → месяц 8).
    assert result["payback_months"] == 8


def test_metrics_no_investment_phase():
    result = _invest_metrics([100.0, 100.0, 100.0], 9.0)
    assert result["payback_months"] == 0


def test_metrics_never_recovers():
    result = _invest_metrics([-1000.0, 10.0, 10.0], 9.0)
    assert result["payback_months"] is None
    assert result["npv"] < 0


def test_irr_matches_breakeven_rate():
    # Поток -100, +121 через 2 месяца: месячный IRR = 10%, годовой ≈ (1.1^12 - 1).
    flows = [-100.0, 0.0, 121.0]
    result = _invest_metrics(flows, 9.0)
    expected_yearly = ((1.1) ** 12 - 1) * 100
    assert result["irr_pct"] == pytest.approx(expected_yearly, abs=0.5)
