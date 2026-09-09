"""Дедупликация при гонке: unique(source_hash) срабатывает на INSERT, импорт выживает.

Симуляция: _hash_exists всегда возвращает False (как будто параллельный импорт
вставил операции между SELECT и INSERT) — дубликаты должны отсечься на уровне
IntegrityError по savepoint, без 500 и без отката всего импорта.
"""
from app import api


def _csv(rows: list[str]) -> bytes:
    header = "Дата;Сумма;Тип;Контрагент;Назначение платежа"
    return "\n".join([header, *rows]).encode("utf-8")


def test_race_duplicates_counted_not_crashed(client, monkeypatch):
    payload_bytes = _csv([
        "05.03.2031;150000;Поступление;ООО Гонка;Оплата по договору Р-1",
        "06.03.2031;-42000;Списание;ООО Аренда-Гонка;Аренда офиса март",
    ])
    first = client.post(
        "/api/imports/upload",
        files={"file": ("race.csv", payload_bytes, "text/csv")},
    ).json()
    assert first["imported"] == 2

    monkeypatch.setattr(api, "_hash_exists", lambda db, op_hash: False)

    second = client.post(
        "/api/imports/upload",
        files={"file": ("race.csv", payload_bytes, "text/csv")},
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["imported"] == 0
    assert payload["duplicates"] == 2


def test_normal_idempotency_still_works(client):
    payload_bytes = _csv([
        "10.04.2031;99000;Поступление;ООО Идемпотент;Оплата счёта 7",
    ])
    first = client.post(
        "/api/imports/upload",
        files={"file": ("idem.csv", payload_bytes, "text/csv")},
    ).json()
    assert first["imported"] == 1
    second = client.post(
        "/api/imports/upload",
        files={"file": ("idem.csv", payload_bytes, "text/csv")},
    ).json()
    assert second["imported"] == 0
    assert second["duplicates"] == 1
