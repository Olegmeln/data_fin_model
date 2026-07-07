"""Смоук-тесты API: полный путь «выписка → категоризация → дашборд → Excel»."""
import io

import openpyxl


def test_health(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["ai_enabled"] is False  # тесты не должны ходить в ИИ


def test_upload_demo_statement(client, sample_csv):
    response = client.post(
        "/api/imports/upload",
        files={"file": ("выписка_demo_июнь_2026.csv", sample_csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("imported", 0) + payload.get("duplicates", 0) > 0


def test_upload_is_idempotent(client, sample_csv):
    first = client.post(
        "/api/imports/upload",
        files={"file": ("выписка_demo_июнь_2026.csv", sample_csv, "text/csv")},
    ).json()
    second = client.post(
        "/api/imports/upload",
        files={"file": ("выписка_demo_июнь_2026.csv", sample_csv, "text/csv")},
    ).json()
    # Повторная загрузка того же файла не создаёт новых операций.
    assert second.get("imported", 0) == 0
    assert second.get("duplicates", 0) >= first.get("imported", 0)


def test_upload_broken_file_returns_client_error(client):
    response = client.post(
        "/api/imports/upload",
        files={"file": ("bad.csv", b"\x00\x01\x02", "text/csv")},
    )
    assert 400 <= response.status_code < 500


def test_dashboard_after_upload(client, sample_csv):
    client.post(
        "/api/imports/upload",
        files={"file": ("выписка_demo_июнь_2026.csv", sample_csv, "text/csv")},
    )
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    body = response.text
    # Дашборд должен содержать инвестиционные метрики (проект = инвестпроект).
    assert "npv" in body


def test_excel_export_is_valid_workbook(client, sample_csv):
    client.post(
        "/api/imports/upload",
        files={"file": ("выписка_demo_июнь_2026.csv", sample_csv, "text/csv")},
    )
    response = client.get("/api/model/export", params={"title": "Тестовый проект"})
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    assert len(workbook.sheetnames) >= 1
