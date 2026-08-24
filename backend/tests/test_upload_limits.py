"""Лимиты загрузки: размер файла, число операций, число файлов интейка."""
import io

from app.config import settings


def test_upload_empty_file_400(client):
    response = client.post(
        "/api/imports/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 400


def test_upload_oversized_file_413(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 100)
    payload = b"a" * 200
    response = client.post(
        "/api/imports/upload",
        files={"file": ("big.csv", payload, "text/csv")},
    )
    assert response.status_code == 413
    assert "лимит" in response.json()["detail"]


def test_upload_too_many_rows_413(client, sample_csv, monkeypatch):
    monkeypatch.setattr(settings, "MAX_STATEMENT_ROWS", 1)
    response = client.post(
        "/api/imports/upload",
        files={"file": ("выписка.csv", sample_csv, "text/csv")},
    )
    assert response.status_code == 413
    assert "лимит" in response.json()["detail"]


def test_intake_too_many_files_413(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_INTAKE_FILES", 1)
    files = [
        ("files", ("a.txt", b"revenue 100", "text/plain")),
        ("files", ("b.txt", b"revenue 200", "text/plain")),
    ]
    response = client.post("/api/intake/extract", params={"project": "demo"}, files=files)
    assert response.status_code == 413


def test_intake_oversized_file_413(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 10)
    files = [("files", ("a.txt", b"x" * 50, "text/plain"))]
    response = client.post("/api/intake/extract", params={"project": "demo"}, files=files)
    assert response.status_code == 413
