"""Тесты схемы assumptions.v1 и хранилища версий."""
from datetime import date

import pytest
from pydantic import ValidationError

from app.finmodel.assumptions_schema import (
    SCHEMA_ID,
    AssumptionSet,
    Capex,
    CapexItem,
    CreditFacility,
    FacilityKind,
    OpenQuestion,
    Product,
    ProjectProfile,
    RatePoint,
    RateSchedule,
    SourceMethod,
    SourceRef,
    Taxes,
    export_json_schema,
)
from app.finmodel.assumptions_store import list_versions, load_assumption_set, save_assumption_set


def synthetic_greenhouse() -> AssumptionSet:
    """Синтетический аналог обучающей пары (структура та же, цифры вымышленные)."""
    return AssumptionSet(
        profile=ProjectProfile(
            name="Тепличный комплекс (синтетика)",
            project_type="строительство→производство→реализация",
            industry="АПК",
            horizon_years=10,
            model_start=date(2025, 1, 1),
        ),
        products=[
            Product(name="Культура, цикл 1", unit="кг", start_price=100,
                    extra={"срок_созревания_мес": 9, "урожайность_кг": 70}),
            Product(name="Культура, цикл 2+", unit="кг", start_price=100,
                    extra={"срок_созревания_мес": 5, "урожайность_кг": 120}),
        ],
        capex=Capex(
            items=[CapexItem(name="Комплекс", amount=1200.0), CapexItem(name="Инфраструктура", amount=150.0)],
            depreciation_months=120,
        ),
        financing={
            "equity_amount": 360.0,
            "facilities": [
                CreditFacility(
                    name="Инвестиционный", kind=FacilityKind.investment, amount=1440.0,
                    term_months=72, grace_months=24,
                    rate=RateSchedule.flat(7.1),
                    rate_formula="ключевая × 70% + 2 п.п.",
                ),
                CreditFacility(
                    name="Оборотный", kind=FacilityKind.working_capital, amount=100.0,
                    term_months=12, rate=RateSchedule.flat(20.0),
                ),
            ],
        },
        taxes=Taxes(
            regime="ТОСЭР",
            profit=RateSchedule(points=[
                RatePoint(value_pct=5), RatePoint(effective_from=date(2030, 1, 1), value_pct=10),
            ]),
            vat=RateSchedule(points=[
                RatePoint(value_pct=20), RatePoint(effective_from=date(2026, 1, 1), value_pct=22),
            ]),
            property_pct=0, land_pct=0,
            payroll_contributions=RateSchedule.flat(7.6),
        ),
        open_questions=[
            OpenQuestion(question="Итог CAPEX: по смете или по модели?", field_path="capex", severity="blocker"),
        ],
        sources={
            "financing.facilities.0.amount": SourceRef(
                method=SourceMethod.extracted, document="бизнес-план.pdf", locator="стр. 9", confidence=0.95,
            ),
            "taxes.vat": SourceRef(method=SourceMethod.user, confidence=1.0),
        },
    )


class TestRateSchedule:
    def test_flat(self):
        assert RateSchedule.flat(9).value_at(date(2030, 5, 1)) == 9

    def test_time_varying_vat(self):
        vat = synthetic_greenhouse().taxes.vat
        assert vat.value_at(date(2025, 12, 31)) == 20
        assert vat.value_at(date(2026, 1, 1)) == 22
        assert vat.value_at(date(2030, 6, 15)) == 22

    def test_points_must_be_sorted(self):
        with pytest.raises(ValidationError):
            RateSchedule(points=[
                RatePoint(effective_from=date(2027, 1, 1), value_pct=1),
                RatePoint(effective_from=date(2026, 1, 1), value_pct=2),
            ])

    def test_undefined_before_first_dated_point(self):
        schedule = RateSchedule(points=[RatePoint(effective_from=date(2026, 1, 1), value_pct=22)])
        with pytest.raises(ValueError):
            schedule.value_at(date(2025, 1, 1))

    def test_single_start_point_only(self):
        with pytest.raises(ValidationError):
            RateSchedule(points=[RatePoint(value_pct=1), RatePoint(value_pct=2)])


class TestAssumptionSet:
    def test_roundtrip_json(self):
        original = synthetic_greenhouse()
        restored = AssumptionSet.from_json(original.to_json())
        assert restored == original

    def test_schema_field_serialized_as_alias(self):
        raw = synthetic_greenhouse().to_json()
        assert '"schema": "finmodel.assumptions.v1"' in raw

    def test_wrong_schema_version_rejected(self):
        data = synthetic_greenhouse().model_dump(by_alias=True)
        data["schema"] = "finmodel.assumptions.v99"
        with pytest.raises(ValidationError):
            AssumptionSet.from_json(data)

    def test_unknown_fields_rejected(self):
        data = synthetic_greenhouse().model_dump(by_alias=True)
        data["surprise"] = 1
        with pytest.raises(ValidationError):
            AssumptionSet.from_json(data)

    def test_duplicate_product_names_rejected(self):
        with pytest.raises(ValidationError):
            AssumptionSet(
                profile=ProjectProfile(name="x"),
                products=[Product(name="A"), Product(name="A")],
            )

    def test_derived_totals(self):
        aset = synthetic_greenhouse()
        assert aset.capex.total == 1350.0
        assert aset.financing.debt_amount == 1540.0
        assert aset.financing.equity_share_pct == pytest.approx(360 / 1900 * 100, abs=1e-6)

    def test_capex_total_override(self):
        capex = Capex(items=[CapexItem(name="x", amount=1)], total_override=999)
        assert capex.total == 999

    def test_minimal_document_valid_with_defaults(self):
        aset = AssumptionSet(profile=ProjectProfile(name="Мини"))
        assert aset.taxes.vat.value_at(date(2026, 1, 1)) == 20
        assert aset.valuation.discount_rate_pct == 9.0

    def test_json_schema_exports(self):
        schema = export_json_schema()
        assert schema["properties"]["schema"]["default"] == SCHEMA_ID
        assert "RateSchedule" in schema["$defs"]


class TestStore:
    def test_versioning_and_load(self, db_session):
        first = save_assumption_set(db_session, "proj-x", synthetic_greenhouse(), comment="из спайка")
        assert (first.version, first.status, first.schema_id) == (1, "draft", SCHEMA_ID)

        second_doc = synthetic_greenhouse()
        second_doc.valuation.discount_rate_pct = 11
        second = save_assumption_set(db_session, "proj-x", second_doc, status="confirmed")
        assert second.version == 2

        loaded = load_assumption_set(db_session, "proj-x")
        assert loaded is not None
        record, doc = loaded
        assert record.version == 2
        assert doc.valuation.discount_rate_pct == 11

        record_v1, doc_v1 = load_assumption_set(db_session, "proj-x", version=1)
        assert record_v1.version == 1
        assert doc_v1.valuation.discount_rate_pct == 9.0

        assert [r.version for r in list_versions(db_session, "proj-x")] == [1, 2]

    def test_missing_project(self, db_session):
        assert load_assumption_set(db_session, "no-such-project") is None
