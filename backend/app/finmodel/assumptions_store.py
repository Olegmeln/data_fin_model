"""Хранилище наборов допущений: версии на проект, чтение/запись."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AssumptionSetRecord
from .assumptions_schema import SCHEMA_ID, AssumptionSet


def save_assumption_set(
    db: Session,
    project_slug: str,
    assumptions: AssumptionSet,
    *,
    status: str = "draft",
    comment: str | None = None,
) -> AssumptionSetRecord:
    """Сохраняет набор как новую версию проекта (версии никогда не перезаписываются)."""
    last = db.execute(
        select(AssumptionSetRecord.version)
        .where(AssumptionSetRecord.project_slug == project_slug)
        .order_by(AssumptionSetRecord.version.desc())
        .limit(1)
    ).scalar()
    record = AssumptionSetRecord(
        project_slug=project_slug,
        version=(last or 0) + 1,
        status=status,
        schema_id=SCHEMA_ID,
        data=assumptions.to_json(),
        comment=comment,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def load_assumption_set(
    db: Session, project_slug: str, version: int | None = None
) -> tuple[AssumptionSetRecord, AssumptionSet] | None:
    """Возвращает (запись, документ) указанной или последней версии."""
    query = select(AssumptionSetRecord).where(AssumptionSetRecord.project_slug == project_slug)
    if version is not None:
        query = query.where(AssumptionSetRecord.version == version)
    else:
        query = query.order_by(AssumptionSetRecord.version.desc()).limit(1)
    record = db.execute(query).scalars().first()
    if record is None:
        return None
    return record, AssumptionSet.from_json(record.data)


def list_versions(db: Session, project_slug: str) -> list[AssumptionSetRecord]:
    return list(
        db.execute(
            select(AssumptionSetRecord)
            .where(AssumptionSetRecord.project_slug == project_slug)
            .order_by(AssumptionSetRecord.version)
        ).scalars()
    )
