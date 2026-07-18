"""REST API сервиса."""
import hashlib
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from . import models
from .categorization.ai import ai_categorize
from .categorization.rules import categorize, learn_rule
from .config import settings
from .db import get_db
from .finmodel.ai_builder import ai_build_assumptions
from .finmodel.builder import build_dashboard
from .finmodel.industries import INDUSTRIES, get_industry, industry_public
from .finmodel.survey import build_survey, compute_rule_series, horizon_from, save_assumptions
from .parsers import ParserError, parse_statement
from .schemas import AssumptionUpsertIn, ConfirmCategoryIn, PlanUpsertIn, SurveyAnswersIn

router = APIRouter()


def _operation_hash(op_date: date, amount, direction: str, counterparty: str, description: str) -> str:
    payload = f"{op_date.isoformat()}|{amount}|{direction}|{(counterparty or '')[:80]}|{(description or '')[:160]}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _operation_to_dict(op: models.Operation) -> dict:
    return {
        "id": op.id,
        "date": op.date.isoformat(),
        "amount": float(op.amount),
        "direction": op.direction,
        "counterparty": op.counterparty,
        "description": op.description,
        "category_code": op.category.code if op.category else None,
        "category_name": op.category.name if op.category else None,
        "confidence": op.confidence,
        "categorized_by": op.categorized_by,
        "status": op.status,
    }


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ai_enabled": settings.ai_enabled,
        "db": settings.db_kind,
        "persistent": settings.db_persistent,
    }


@router.get("/model/export")
def export_model(title: str = "Новый проект", db: Session = Depends(get_db)):
    """Скачивание файла финансовой модели (.xlsx) с живыми формулами."""
    from urllib.parse import quote

    from fastapi.responses import Response

    from .finmodel.excel_export import export_model_bytes
    from .finmodel.industries import TAX_RATES

    drivers: dict = {}
    profile = db.query(models.BusinessProfile).first()
    if profile is not None:
        answers = json.loads(profile.answers_json or "{}")

        def num(key):
            value = answers.get(key)
            try:
                return float(str(value).replace(" ", "").replace(",", ".")) if value not in (None, "") else None
            except ValueError:
                return None

        mapping = {
            "base_revenue": num("monthly_revenue"),
            "payroll": num("payroll_monthly"),
            "rent": num("rent_monthly"),
            "capex_total": num("capex_total"),
            "loan_amount": num("loan_amount"),
        }
        drivers = {key: value for key, value in mapping.items() if value}
        if num("loan_rate"):
            drivers["loan_rate"] = num("loan_rate") / 100
        if num("discount_rate"):
            drivers["discount_rate"] = num("discount_rate") / 100
        if answers.get("tax_mode") in TAX_RATES:
            drivers["tax_rate"] = TAX_RATES[answers["tax_mode"]][0]
        if answers.get("business_age") == "new":
            drivers["ramp_months"] = 4

    content = export_model_bytes(title=title.strip() or "Новый проект", drivers=drivers)
    filename = f"Финмодель_{title.strip() or 'проект'}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=model.xlsx; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    categories = db.query(models.Category).order_by(models.Category.id).all()
    return [{"code": c.code, "name": c.name, "kind": c.kind} for c in categories]


# ------------------------------------------------- Опросник и допущения ----

@router.get("/survey")
def get_survey() -> dict:
    """Структура интерактивного опросника (вопросы, ветвления, отрасли)."""
    return build_survey()


@router.post("/survey")
def submit_survey(body: SurveyAnswersIn, db: Session = Depends(get_db)) -> dict:
    """Сохранение ответов опросника и сборка модели: правила → уточнение ИИ."""
    answers = body.answers or {}
    industry_code = str(answers.get("industry") or "")
    industry = get_industry(industry_code)
    if industry is None:
        raise HTTPException(status_code=400, detail="Не выбрана отрасль (вопрос «industry»).")

    horizon = horizon_from(answers)
    draft_rows = compute_rule_series(industry, answers, horizon)
    ai_result = ai_build_assumptions(industry, answers, horizon, draft_rows)
    rows = ai_result["rows"] if ai_result else draft_rows
    created = save_assumptions(db, rows, horizon)

    answers["_ai_summary"] = ai_result["summary"] if ai_result else None

    profile = db.query(models.BusinessProfile).first()
    if profile is None:
        profile = models.BusinessProfile(industry_code=industry_code)
        db.add(profile)
    profile.industry_code = industry_code
    profile.answers_json = json.dumps(answers, ensure_ascii=False)

    db.commit()
    return {
        "industry": industry_public(industry),
        "assumptions_created": created,
        "horizon": horizon,
        "ai_used": bool(ai_result),
        "ai_summary": ai_result["summary"] if ai_result else None,
        "answers": answers,
    }


@router.get("/profile")
def get_profile(db: Session = Depends(get_db)) -> dict:
    """Текущий профиль бизнеса (или null, если опросник не пройден)."""
    profile = db.query(models.BusinessProfile).first()
    if profile is None:
        return {"profile": None}
    industry = get_industry(profile.industry_code)
    return {
        "profile": {
            "industry": industry_public(industry) if industry else {"code": profile.industry_code},
            "answers": json.loads(profile.answers_json or "{}"),
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }
    }


@router.get("/industries")
def list_industries() -> list[dict]:
    return [industry_public(i) for i in INDUSTRIES]


@router.get("/assumptions")
def list_assumptions(db: Session = Depends(get_db)) -> list[dict]:
    items = (
        db.query(models.Assumption)
        .order_by(models.Assumption.month, models.Assumption.category_id)
        .all()
    )
    return [
        {
            "id": a.id,
            "category_code": a.category.code,
            "category_name": a.category.name,
            "month": a.month.strftime("%Y-%m"),
            "amount": float(a.amount),
            "source": a.source,
            "note": a.note,
        }
        for a in items
    ]


@router.put("/assumptions")
def upsert_assumptions(body: AssumptionUpsertIn, db: Session = Depends(get_db)) -> dict:
    """Правка допущений пользователем (приоритетнее сгенерированных опросником)."""
    category_by_code = {c.code: c for c in db.query(models.Category).all()}
    saved = 0
    for item in body.items:
        category = category_by_code.get(item.category_code)
        if category is None:
            raise HTTPException(status_code=404, detail=f"Статья {item.category_code} не найдена.")
        try:
            month = datetime.strptime(item.month, "%Y-%m").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Месяц задаётся в формате YYYY-MM.") from exc
        assumption = (
            db.query(models.Assumption)
            .filter(models.Assumption.category_id == category.id, models.Assumption.month == month)
            .first()
        )
        if item.amount <= 0:
            if assumption is not None:
                db.delete(assumption)
                saved += 1
            continue
        if assumption is None:
            assumption = models.Assumption(category_id=category.id, month=month, amount=item.amount)
            db.add(assumption)
        assumption.amount = item.amount
        assumption.source = "user"
        if item.note is not None:
            assumption.note = item.note
        saved += 1
    db.commit()
    return {"saved": saved}


@router.delete("/assumptions/{assumption_id}")
def delete_assumption(assumption_id: int, db: Session = Depends(get_db)) -> dict:
    assumption = db.query(models.Assumption).get(assumption_id)
    if assumption is None:
        raise HTTPException(status_code=404, detail="Допущение не найдено.")
    db.delete(assumption)
    db.commit()
    return {"deleted": assumption_id}


# ---------------------------------------------------------------- Импорт ----

@router.post("/imports/upload")
async def upload_statement(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    """Загрузка выписки: парсинг → дедупликация → категоризация (правила + ИИ)."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Файл пуст.")

    try:
        fmt, parsed = parse_statement(file.filename, raw)
    except ParserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    log = models.ImportLog(filename=file.filename or "statement", fmt=fmt, total_rows=len(parsed))
    db.add(log)
    db.flush()

    categories = db.query(models.Category).all()
    category_by_code = {c.code: c for c in categories}
    user_rules = db.query(models.Rule).all()

    new_operations: list[models.Operation] = []
    duplicates = 0
    seen_hashes: set[str] = set()

    for item in parsed:
        op_hash = _operation_hash(item.date, item.amount, item.direction, item.counterparty, item.description)
        if op_hash in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(op_hash)
        if db.query(models.Operation.id).filter(models.Operation.source_hash == op_hash).first():
            duplicates += 1
            continue

        code, confidence, source = categorize(item.direction, item.counterparty, item.description, user_rules)
        category = category_by_code.get(code)
        operation = models.Operation(
            date=item.date,
            amount=item.amount,
            direction=item.direction,
            counterparty=item.counterparty or None,
            description=item.description or None,
            category_id=category.id if category else None,
            confidence=confidence,
            categorized_by=source,
            status="auto" if confidence >= settings.CONFIDENCE_THRESHOLD else "needs_review",
            source_hash=op_hash,
            import_id=log.id,
        )
        new_operations.append(operation)

    # ИИ-проход по операциям с низкой уверенностью (если задан ключ API)
    pending = [op for op in new_operations if op.status == "needs_review"]
    if settings.ai_enabled and pending:
        items = [
            {
                "id": index,
                "direction": op.direction,
                "amount": float(op.amount),
                "counterparty": op.counterparty or "",
                "description": (op.description or "")[:200],
            }
            for index, op in enumerate(pending)
        ]
        category_dicts = [{"code": c.code, "name": c.name, "kind": c.kind} for c in categories]
        ai_results = await ai_categorize(items, category_dicts)
        for index, (code, confidence) in ai_results.items():
            operation = pending[index]
            category = category_by_code.get(code)
            if category is None:
                continue
            operation.category_id = category.id
            operation.confidence = confidence
            operation.categorized_by = "ai"
            if confidence >= settings.CONFIDENCE_THRESHOLD:
                operation.status = "auto"

    db.add_all(new_operations)
    log.imported = len(new_operations)
    log.duplicates = duplicates
    log.needs_review = sum(1 for op in new_operations if op.status == "needs_review")
    db.commit()

    return {
        "import_id": log.id,
        "format": fmt,
        "total_rows": log.total_rows,
        "imported": log.imported,
        "duplicates": log.duplicates,
        "needs_review": log.needs_review,
        "ai_used": settings.ai_enabled and bool(pending),
    }


@router.get("/imports")
def list_imports(db: Session = Depends(get_db)) -> list[dict]:
    logs = db.query(models.ImportLog).order_by(models.ImportLog.created_at.desc()).limit(20).all()
    return [
        {
            "id": log.id,
            "filename": log.filename,
            "format": log.fmt,
            "imported": log.imported,
            "duplicates": log.duplicates,
            "needs_review": log.needs_review,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


# ------------------------------------------------------------- Операции ----

@router.get("/operations")
def list_operations(
    status: str | None = None,
    month: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(models.Operation)
    if status:
        query = query.filter(models.Operation.status == status)
    if category:
        query = query.join(models.Category).filter(models.Category.code == category)
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Месяц задаётся в формате YYYY-MM.") from exc
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        query = query.filter(models.Operation.date >= start, models.Operation.date < end)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            models.Operation.description.ilike(pattern) | models.Operation.counterparty.ilike(pattern)
        )

    total = query.count()
    operations = (
        query.order_by(models.Operation.date.desc(), models.Operation.id.desc())
        .offset(offset)
        .limit(min(limit, 500))
        .all()
    )
    return {"total": total, "items": [_operation_to_dict(op) for op in operations]}


@router.patch("/operations/{operation_id}")
def confirm_category(
    operation_id: int,
    body: ConfirmCategoryIn,
    db: Session = Depends(get_db),
) -> dict:
    """Подтверждение/смена статьи. Создаёт правило и применяет его к похожим операциям."""
    operation = db.query(models.Operation).get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Операция не найдена.")
    category = db.query(models.Category).filter(models.Category.code == body.category_code).first()
    if category is None:
        raise HTTPException(status_code=404, detail="Статья не найдена.")

    operation.category_id = category.id
    operation.confidence = 1.0
    operation.categorized_by = "user"
    operation.status = "confirmed"
    learn_rule(db, operation)

    applied = 0
    if body.apply_to_similar and operation.counterparty:
        similar = (
            db.query(models.Operation)
            .filter(
                models.Operation.id != operation.id,
                models.Operation.counterparty == operation.counterparty,
                models.Operation.status != "confirmed",
            )
            .all()
        )
        for other in similar:
            other.category_id = category.id
            other.confidence = 0.95
            other.categorized_by = "rule"
            other.status = "auto"
            applied += 1

    db.commit()
    return {"operation": _operation_to_dict(operation), "applied_to_similar": applied}


# ------------------------------------------------------- Дашборд и план ----

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict:
    return build_dashboard(db)


@router.put("/plan")
def upsert_plan(body: PlanUpsertIn, db: Session = Depends(get_db)) -> dict:
    """Внесение плановых значений по статьям (upsert по паре статья+месяц)."""
    category_by_code = {c.code: c for c in db.query(models.Category).all()}
    saved = 0
    for item in body.items:
        category = category_by_code.get(item.category_code)
        if category is None:
            raise HTTPException(status_code=404, detail=f"Статья {item.category_code} не найдена.")
        try:
            month = datetime.strptime(item.month, "%Y-%m").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Месяц задаётся в формате YYYY-MM.") from exc
        plan = (
            db.query(models.PlanValue)
            .filter(models.PlanValue.category_id == category.id, models.PlanValue.month == month)
            .first()
        )
        if plan is None:
            plan = models.PlanValue(category_id=category.id, month=month, amount=item.amount)
            db.add(plan)
        else:
            plan.amount = item.amount
        saved += 1
    db.commit()
    return {"saved": saved}


# --------------------------------------------------------------- Параметры ----
# Ключевые драйверы модели, редактируемые на листе «Параметры». Пишутся в
# BusinessProfile.answers (как ответы опросника) и пересобирают допущения тем же
# движком, что и опросник — правки пользователя в сетке (source='user') при этом
# сохраняются (см. save_assumptions).

_PARAM_KEYS = [
    "industry", "legal_form", "business_age", "planning_horizon",
    "monthly_revenue", "employees", "payroll_monthly", "rent_monthly",
    "tax_mode", "capex_total", "funding_source", "loan_amount", "loan_rate",
    "discount_rate",
]


@router.get("/parameters")
def get_parameters(db: Session = Depends(get_db)) -> dict:
    """Текущие драйверы модели + описания полей для формы «Параметры»."""
    profile = db.query(models.BusinessProfile).first()
    answers = json.loads(profile.answers_json or "{}") if profile else {}
    industry = get_industry(profile.industry_code) if profile else None
    return {
        "answers": {k: answers.get(k) for k in _PARAM_KEYS if answers.get(k) not in (None, "")},
        "industry_code": profile.industry_code if profile else None,
        "industry": industry_public(industry) if industry else None,
        "horizon": horizon_from(answers),
        "questions": [q for q in build_survey()["questions"] if q["id"] in _PARAM_KEYS],
        "industries": [{"code": i["code"], "name": i["name"]} for i in INDUSTRIES],
    }


@router.put("/parameters")
def put_parameters(body: SurveyAnswersIn, db: Session = Depends(get_db)) -> dict:
    """Обновление драйверов модели и пересборка допущений (правила шаблона).

    Тело: {"answers": {ключ: значение, ...}} — частичное или полное. Значения
    сливаются с текущим профилем; отрасль обязательна.
    """
    profile = db.query(models.BusinessProfile).first()
    answers = json.loads(profile.answers_json or "{}") if profile else {}
    for key, value in (body.answers or {}).items():
        answers[key] = value

    industry_code = str(answers.get("industry") or (profile.industry_code if profile else "") or "")
    industry = get_industry(industry_code)
    if industry is None:
        raise HTTPException(status_code=400, detail="Не задана отрасль (параметр «industry»).")
    answers["industry"] = industry_code

    horizon = horizon_from(answers)
    rows = compute_rule_series(industry, answers, horizon)
    created = save_assumptions(db, rows, horizon)

    if profile is None:
        profile = models.BusinessProfile(industry_code=industry_code)
        db.add(profile)
    profile.industry_code = industry_code
    profile.answers_json = json.dumps(answers, ensure_ascii=False)
    db.commit()

    return {
        "answers": {k: answers.get(k) for k in _PARAM_KEYS if answers.get(k) not in (None, "")},
        "industry": industry_public(industry),
        "horizon": horizon,
        "assumptions_created": created,
    }


# ------------------------------------------------- Интейк документов (v1) ----

from .finmodel.assumptions_schema import AssumptionSet  # noqa: E402
from .finmodel.intake import IntakeError, extract_assumptions, extract_text  # noqa: E402
from .finmodel.intake.preferences import apply_preferences, store_preferences  # noqa: E402
from .finmodel.intake.validator import apply_validation  # noqa: E402
from .finmodel.assumptions_store import (  # noqa: E402
    list_versions, load_assumption_set, save_assumption_set,
)


def _record_out(record, assumptions: AssumptionSet) -> dict:
    return {
        "project": record.project_slug,
        "version": record.version,
        "status": record.status,
        "schema": record.schema_id,
        "comment": record.comment,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "assumptions": assumptions.model_dump(by_alias=True, exclude_none=True, mode="json"),
        "open_questions": [q.model_dump(exclude_none=True) for q in assumptions.open_questions],
    }


@router.post("/intake/extract")
async def intake_extract(
    project: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Документы → извлечение → память предпочтений → валидация → черновик набора."""
    documents = []
    for upload in files:
        raw = await upload.read()
        try:
            documents.append(extract_text(upload.filename or "документ", raw))
        except IntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        assumptions = extract_assumptions(documents)
    except IntakeError as exc:
        status = 503 if "ANTHROPIC_API_KEY" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    assumptions = apply_preferences(db, assumptions)
    assumptions = apply_validation(assumptions)
    record = save_assumption_set(
        db, project, assumptions,
        comment=f"извлечено из: {', '.join(d.filename for d in documents)}",
    )
    return _record_out(record, assumptions)


@router.get("/assumption-sets/{project}")
def get_assumption_set(project: str, version: int | None = None, db: Session = Depends(get_db)) -> dict:
    loaded = load_assumption_set(db, project, version)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Наборы допущений проекта {project!r} не найдены.")
    record, assumptions = loaded
    return _record_out(record, assumptions)


@router.get("/assumption-sets/{project}/versions")
def get_assumption_versions(project: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "version": r.version,
            "status": r.status,
            "comment": r.comment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in list_versions(db, project)
    ]


@router.put("/assumption-sets/{project}")
def put_assumption_set(project: str, body: dict, db: Session = Depends(get_db)) -> dict:
    """Сохранение правок пользователя новой версией; confirmed пополняет память."""
    status = body.get("status", "draft")
    if status not in ("draft", "confirmed"):
        raise HTTPException(status_code=400, detail="status должен быть draft или confirmed")
    try:
        assumptions = AssumptionSet.from_json(body.get("assumptions") or {})
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Набор не прошёл схему: {exc}") from exc
    assumptions = apply_validation(assumptions)
    blockers = [q for q in assumptions.open_questions if q.severity == "blocker"]
    if status == "confirmed" and blockers:
        raise HTTPException(
            status_code=409,
            detail={"message": "Нельзя подтвердить набор с blocker-вопросами",
                    "open_questions": [q.model_dump(exclude_none=True) for q in blockers]},
        )
    record = save_assumption_set(db, project, assumptions, status=status, comment=body.get("comment"))
    if status == "confirmed":
        store_preferences(db, assumptions)
    return _record_out(record, assumptions)
