"""Тесты моста «опросник → assumptions.v1»."""
import pytest

from app.finmodel.from_survey import assumption_set_from_survey

ANSWERS = {
    "industry": "services",
    "business_age": "new",
    "planning_horizon": "24",
    "monthly_revenue": "800 000",
    "payroll_monthly": "200000",
    "rent_monthly": "100000",
    "tax_mode": "usn15",
    "capex_total": "1000000",
    "funding_source": "loan",
    "loan_amount": "700000",
    "loan_rate": "17",
    "discount_rate": "20",
}


class TestConverter:
    def test_full_profile_maps_all_sections(self):
        aset = assumption_set_from_survey(ANSWERS, name="тест")
        assert aset.profile.horizon_years == 2
        assert aset.profile.industry == "Услуги / агентство"
        assert aset.profile.project_type == "запуск"
        # выручка → продукт с разгоном для запуска
        assert aset.products[0].start_price == 800000
        assert aset.products[0].ramp_up_months == 4
        # расходы
        assert {i.name for i in aset.opex.items} == {"ФОТ", "Аренда и коммунальные"}
        # CAPEX и финансирование: 1 млн CAPEX = 700 кредит + 300 собственные
        assert aset.capex.total_override == 1000000
        assert aset.financing.facilities[0].amount == 700000
        assert aset.financing.facilities[0].rate.points[0].value_pct == 17
        assert aset.financing.equity_amount == pytest.approx(300000)
        # налоги и оценка
        assert aset.taxes.profit.points[0].value_pct == 15
        assert aset.valuation.discount_rate_pct == 20
        # сценарии двигают цену
        names = [s.name for s in aset.scenarios]
        assert names == ["Пессимистичный", "Базовый", "Оптимистичный"]
        assert aset.scenarios[0].overrides["products.0.start_price"] == pytest.approx(640000)

    def test_sources_are_derived(self):
        aset = assumption_set_from_survey(ANSWERS)
        for section in ("profile", "products", "opex", "capex", "financing", "taxes", "valuation"):
            assert aset.sources[section].method.value == "derived", section

    def test_minimal_answers_still_valid(self):
        aset = assumption_set_from_survey({"industry": "services"})
        assert aset.profile.horizon_years == 1
        assert aset.products == []
        assert aset.financing.facilities == []
        assert aset.scenarios[0].name == "Базовый"

    def test_own_funding_skips_credit(self):
        answers = dict(ANSWERS, funding_source="own", loan_amount="700000")
        aset = assumption_set_from_survey(answers)
        assert aset.financing.facilities == []
        assert aset.financing.equity_amount == pytest.approx(1000000)


class TestApi:
    def test_404_without_survey(self, client):
        assert client.post("/api/assumption-sets/srv/from-survey").status_code == 404

    def test_survey_then_convert(self, client):
        response = client.post("/api/survey", json={"answers": dict(ANSWERS)})
        assert response.status_code == 200, response.text
        converted = client.post("/api/assumption-sets/srv2/from-survey")
        assert converted.status_code == 200, converted.text
        payload = converted.json()
        assert payload["version"] == 1 and payload["comment"] == "из опросника"
        assert payload["assumptions"]["products"][0]["start_price"] == 800000
        # книга собирается из результата моста
        metrics = client.get("/api/book/srv2/metrics").json()["metrics"]
        assert metrics["npv"] is not None
        stress = client.get("/api/book/srv2/metrics",
                            params={"scenario": "Пессимистичный"}).json()["metrics"]
        assert stress["revenue_total"] < metrics["revenue_total"]
