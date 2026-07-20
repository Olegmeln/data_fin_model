"""Дедупликация при гонке: unique(source_hash) срабатывает на INSERT, импорт выживает.

Симуляция: _hash_exists всегда возвращает False (как будто параллельный импорт
вставил операции между SELECT и INSERT) — дубликаты должны отсечься на уровне
IntegrityError по savepoint, без 500 и без отката всего импорта.
"""
from app import api


def test_race_duplicates_counted_not_crashed(client, sample_csv, monkeypatch):
    first = client.post(
        "/api/imports/upload",
        files={"file": ("выписка.csv", sample_csv, "text/csv")},
    ).json()
    assert first["imported"] > 0

    monkeypatch.setattr(api, "_hash_exists", lambda db, op_hash: False)

    second = client.post(
        "/api/imports/upload",
        files={"file": ("выписка.csv", sample_csv, "text/csv")},
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["imported"] == 0
    assert payload["duplicates"] >= first["imported"]


def test_normal_idempotency_still_works(client, sample_csv):
    first = client.post(
        "/api/imports/upload",
        files={"file": ("выписка2.csv", sample_csv, "text/csv")},
    ).json()
    second = client.post(
        "/api/imports/upload",
        files={"file": ("выписка2.csv", sample_csv, "text/csv")},
    ).json()
    assert second["imported"] == 0
    assert second["duplicates"] == first["imported"] + first["duplicates"]
