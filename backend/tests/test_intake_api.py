"""Тесты API интейка (с подменённым LLM) и памяти предпочтений."""
import json

import pytest

import app.api as api_module
from app.finmodel.assumptions_schema import (
    AssumptionSet, Product, ProjectProfile, RateSchedule, SourceMethod, SourceRef, Taxes,
)
from app.finmodel.intake.preferences import apply_preferences, profile_key_of, store_preferences


def _extracted_set(industry: str = "пищевое производство", with_taxes_source: bool = False) -> AssumptionSet:
    sources = {
        "products.0.start_price": SourceRef(method=SourceMethod.extracted,
                                            document="план.txt", confidence=0.9),
    }
    if with_taxes_source:
        sources["taxes.vat"] = SourceRef(method=SourceMethod.extracted, document="план.txt", confidence=0.9)
    return AssumptionSet(
        profile=ProjectProfile(name="Пастила", industry=industry, horizon_years=7),
        products=[Product(name="Пастила", start_price=900)],
        financing={"equity_amount": 30, "facilities": []},
        sources=sources,
    )


@pytest.fixture()
def fake_extraction(monkeypatch):
    """Подменяет LLM-извлечение готовым набором (без сети)."""
    def _install(aset: AssumptionSet):
        monkeypatch.setattr(api_module, "extract_assumptions", lambda docs: aset.model_copy(deep=True))
    return _install


class TestIntakeApi:
    def test_extract_creates_draft_version(self, client, fake_extraction):
        fake_extraction(_extracted_set())
        response = client.post(
            "/api/intake/extract?project=pastila",
            files=[("files", ("план.txt", "Мини-план".encode(), "text/plain"))],
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["project"] == "pastila"
        assert payload["status"] == "draft"
        assert payload["comment"].startswith("извлечено из: план.txt")
        # валидатор добавил вопросы (нет CAPEX-покрытия и т.п. нет, но цена есть)
        assert isinstance(payload["open_questions"], list)

    def test_extract_bad_file_400(self, client):
        response = client.post(
            "/api/intake/extract?project=pastila",
            files=[("files", ("план.xyz", b"data", "application/octet-stream"))],
        )
        assert response.status_code == 400
        assert "не поддерживается" in response.json()["detail"]

    def test_extract_without_key_503(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
        response = client.post(
            "/api/intake/extract?project=pastila",
            files=[("files", ("план.txt", "текст".encode(), "text/plain"))],
        )
        assert response.status_code == 503
        assert "ANTHROPIC_API_KEY" in response.json()["detail"]

    def test_versions_and_get(self, client, fake_extraction):
        fake_extraction(_extracted_set())
        client.post("/api/intake/extract?project=verspr",
                    files=[("files", ("а.txt", b"x", "text/plain"))])
        client.post("/api/intake/extract?project=verspr",
                    files=[("files", ("б.txt", b"y", "text/plain"))])
        versions = client.get("/api/assumption-sets/verspr/versions").json()
        assert [v["version"] for v in versions] == [1, 2]
        latest = client.get("/api/assumption-sets/verspr").json()
        assert latest["version"] == 2
        first = client.get("/api/assumption-sets/verspr", params={"version": 1}).json()
        assert first["version"] == 1

    def test_get_missing_404(self, client):
        assert client.get("/api/assumption-sets/nope").status_code == 404

    def test_put_confirm_with_blockers_409(self, client):
        aset = _extracted_set()
        body = {"assumptions": aset.model_dump(by_alias=True, exclude_none=True, mode="json"),
                "status": "confirmed"}
        body["assumptions"]["products"] = []  # спровоцируем blocker «нет продуктов»
        response = client.put("/api/assumption-sets/blocked", json=body)
        assert response.status_code == 409
        assert any(q["severity"] == "blocker"
                   for q in response.json()["detail"]["open_questions"])

    def test_put_confirmed_saves_and_remembers(self, client):
        aset = _extracted_set()
        body = {"assumptions": aset.model_dump(by_alias=True, exclude_none=True, mode="json"),
                "status": "confirmed", "comment": "проверено"}
        response = client.put("/api/assumption-sets/pastila-ok", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "confirmed"

    def test_put_invalid_schema_422(self, client):
        response = client.put("/api/assumption-sets/bad", json={"assumptions": {"schema": "x"}})
        assert response.status_code == 422


class TestAutoAssumptionSet:
    """Auto-режим формы допущений: стартовый профиль + память предпочтений."""

    def test_auto_creates_default_profile(self, client):
        response = client.post("/api/assumption-sets/autoproj/auto")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["version"] == 1 and payload["status"] == "draft"
        a = payload["assumptions"]
        # дефолты Архитектуры_финмодели_v1: 5 лет, 9%, 2 товара + 2 услуги, 3 сценария
        assert a["profile"]["horizon_years"] == 5
        assert a["valuation"]["discount_rate_pct"] == 9.0
        kinds = sorted(p["kind"] for p in a["products"])
        assert kinds == ["goods", "goods", "service", "service"]
        assert [s["name"] for s in a["scenarios"]] == ["Пессимистичный", "Базовый", "Оптимистичный"]
        assert a["sources"]["profile"]["method"] == "default"

    def test_auto_applies_preference_memory(self, client, db_session):
        # подтверждённый набор «универсального» профиля пополняет память
        confirmed = _extracted_set(industry=None).model_copy(update={"taxes": Taxes(
            regime="УСН 15%", vat=RateSchedule.flat(0), profit=RateSchedule.flat(15))})
        store_preferences(db_session, confirmed)
        # auto-набор без отрасли = тот же «универсальный» профиль → налоги из памяти
        response = client.post("/api/assumption-sets/autoprefs/auto")
        assert response.status_code == 200, response.text
        a = response.json()["assumptions"]
        assert a["taxes"]["regime"] == "УСН 15%"
        assert "память предпочтений" in a["sources"]["taxes"]["note"]

    def test_auto_versions_stack(self, client):
        client.post("/api/assumption-sets/autostack/auto")
        response = client.post("/api/assumption-sets/autostack/auto")
        assert response.json()["version"] == 2


class TestPreferenceMemory:
    def test_store_and_apply_for_same_profile(self, db_session):
        confirmed = _extracted_set()
        confirmed = confirmed.model_copy(update={"taxes": Taxes(
            regime="УСН 15%", vat=RateSchedule.flat(0), profit=RateSchedule.flat(15))})
        store_preferences(db_session, confirmed)

        fresh = _extracted_set()  # тот же профиль, налоги не извлечены
        enriched = apply_preferences(db_session, fresh)
        assert enriched.taxes.regime == "УСН 15%"
        source = enriched.sources["taxes"]
        assert source.method == SourceMethod.default
        assert "память предпочтений" in (source.note or "")

    def test_extracted_section_wins_over_memory(self, db_session):
        confirmed = _extracted_set().model_copy(update={"taxes": Taxes(regime="УСН 15%")})
        store_preferences(db_session, confirmed)

        fresh = _extracted_set(with_taxes_source=True)  # налоги извлечены из документа
        enriched = apply_preferences(db_session, fresh)
        assert enriched.taxes.regime is None  # память не перекрыла документ

    def test_other_profile_untouched(self, db_session):
        confirmed = _extracted_set(industry="АПК").model_copy(
            update={"taxes": Taxes(regime="ТОСЭР")})
        store_preferences(db_session, confirmed)

        fresh = _extracted_set(industry="ИТ-услуги")
        enriched = apply_preferences(db_session, fresh)
        assert enriched.taxes.regime is None

    def test_confirm_count_grows(self, db_session):
        from sqlalchemy import select
        from app.models import PreferenceMemory

        aset = _extracted_set(industry="изолированный-профиль-счётчика").model_copy(
            update={"taxes": Taxes(regime="УСН 15%")})
        store_preferences(db_session, aset)
        store_preferences(db_session, aset)
        record = db_session.execute(
            select(PreferenceMemory).where(
                PreferenceMemory.profile_key == profile_key_of(aset),
                PreferenceMemory.field_path == "taxes")
        ).scalars().one()
        assert record.confirmed_count == 2

    def test_roundtrip_value_is_valid_json(self, db_session):
        aset = _extracted_set().model_copy(update={"taxes": Taxes(regime="УСН 15%")})
        store_preferences(db_session, aset)
        from sqlalchemy import select
        from app.models import PreferenceMemory
        record = db_session.execute(select(PreferenceMemory)).scalars().first()
        assert json.loads(record.value)["regime"] == "УСН 15%"
