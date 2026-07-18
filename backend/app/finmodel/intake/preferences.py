"""Память предпочтений: изменения пользователя становятся дефолтами
для аналогичных задач (профиль = отрасль проекта).

MVP-правило: при подтверждении набора запоминаем разделы taxes и valuation;
при извлечении нового набора того же профиля подставляем их, если раздел
не был извлечён из документов (в sources нет путей раздела), и помечаем
источник method=default с пометкой «память предпочтений».
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import PreferenceMemory
from ..assumptions_schema import AssumptionSet, SourceMethod, SourceRef

REMEMBERED_SECTIONS = ("taxes", "valuation")


def profile_key_of(aset: AssumptionSet) -> str:
    return (aset.profile.industry or "универсальный").strip().lower()


def _section_extracted(aset: AssumptionSet, section: str) -> bool:
    """Раздел считается извлечённым, если на него есть хоть один источник."""
    prefix = section + "."
    return any(path == section or path.startswith(prefix) for path in aset.sources)


def store_preferences(db: Session, aset: AssumptionSet) -> int:
    """Запоминает разделы подтверждённого набора; возвращает число записей."""
    key = profile_key_of(aset)
    data = aset.model_dump(by_alias=True, exclude_none=True, mode="json")
    saved = 0
    for section in REMEMBERED_SECTIONS:
        payload = json.dumps(data.get(section, {}), ensure_ascii=False)
        record = db.execute(
            select(PreferenceMemory).where(
                PreferenceMemory.profile_key == key, PreferenceMemory.field_path == section
            )
        ).scalars().first()
        if record is None:
            db.add(PreferenceMemory(profile_key=key, field_path=section, value=payload))
        else:
            if record.value != payload:
                record.value = payload
            record.confirmed_count += 1
        saved += 1
    db.commit()
    return saved


def apply_preferences(db: Session, aset: AssumptionSet) -> AssumptionSet:
    """Подставляет запомненные разделы в набор, где они не извлечены из документов."""
    key = profile_key_of(aset)
    records = db.execute(
        select(PreferenceMemory).where(PreferenceMemory.profile_key == key)
    ).scalars().all()
    if not records:
        return aset

    update: dict = {}
    sources = dict(aset.sources)
    for record in records:
        if record.field_path not in REMEMBERED_SECTIONS:
            continue
        if _section_extracted(aset, record.field_path):
            continue  # документ важнее памяти
        update[record.field_path] = json.loads(record.value)
        sources[record.field_path] = SourceRef(
            method=SourceMethod.default,
            note=f"память предпочтений (подтверждено ×{record.confirmed_count})",
        )
    if not update:
        return aset
    data = aset.model_dump(by_alias=True, exclude_none=True, mode="json")
    data.update(update)
    data["sources"] = {k: (v.model_dump(exclude_none=True, mode="json") if isinstance(v, SourceRef) else v)
                       for k, v in sources.items()}
    return AssumptionSet.from_json(data)
