"""Жизненный цикл БД: переключатель DB_AUTO_INIT, check_db, health."""
from app.config import settings


class TestDbAutoInitSwitch:
    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("DB_AUTO_INIT", "0")
        assert settings.db_auto_init is False

    def test_explicit_on(self, monkeypatch):
        monkeypatch.setenv("DB_AUTO_INIT", "1")
        monkeypatch.setenv("VERCEL", "1")  # явное значение сильнее платформы
        assert settings.db_auto_init is True

    def test_default_off_on_vercel(self, monkeypatch):
        monkeypatch.delenv("DB_AUTO_INIT", raising=False)
        monkeypatch.setenv("VERCEL", "1")
        assert settings.db_auto_init is False

    def test_default_on_locally(self, monkeypatch):
        monkeypatch.delenv("DB_AUTO_INIT", raising=False)
        monkeypatch.delenv("VERCEL", raising=False)
        assert settings.db_auto_init is True


def test_check_db_ok(client):
    from app.db import check_db

    assert check_db() is True


def test_health_reports_db_ok(client):
    data = client.get("/api/health").json()
    assert data["db_ok"] is True


class TestCorsOrigins:
    def test_default_wildcard(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        assert settings.cors_origins == ["*"]

    def test_parsing_list(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example ,")
        assert settings.cors_origins == ["https://a.example", "https://b.example"]
