"""Двусторонняя синхронизация финмодели с Google Sheets.

push_model — генерирует тот же полноформатный .xlsx, что и экспорт (живые
формулы, именованные диапазоны, 7 листов), и загружает его в Google Drive с
конвертацией в нативную Google-таблицу. Повторный push обновляет тот же файл,
поэтому ссылка на модель не меняется.

pull_edits — читает ключевые драйверы-входы с листа «Допущения» и возвращает
их в профиль бизнеса; при следующей сборке модели они снова попадут в расчёт.
Так реализуется двусторонний цикл на уровне входных данных модели.
"""
import io
import json
from datetime import datetime

from googleapiclient.http import MediaIoBaseUpload

from .. import models
from . import google_client
from .drivers import drivers_from_profile
from .excel_export import export_model_bytes
from .industries import INDUSTRIES

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
ASSUMPTIONS_SHEET = "Допущения"

# Ячейка листа «Допущения» → (ключ ответа опросника, множитель).
# Множитель 100 переводит долю (0.17) в проценты (17) — так хранит опросник.
PULL_CELL_MAP: dict[str, tuple[str, float]] = {
    "B21": ("monthly_revenue", 1),
    "B31": ("payroll_monthly", 1),
    "B32": ("rent_monthly", 1),
    "B38": ("capex_total", 1),
    "B42": ("loan_amount", 1),
    "B43": ("loan_rate", 100),
    "B6": ("discount_rate", 100),
}


def _sheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def push_model(db, title: str = "Финмодель") -> dict:
    """Создаёт или обновляет Google-таблицу модели из текущего профиля."""
    drive = google_client.drive_service(db)
    profile = db.query(models.BusinessProfile).first()
    xlsx = export_model_bytes(title=title, drivers=drivers_from_profile(profile))
    media = MediaIoBaseUpload(io.BytesIO(xlsx), mimetype=XLSX_MIME, resumable=False)

    link = db.query(models.SheetLink).first()
    if link and link.spreadsheet_id:
        # Тот же fileId → ссылка на модель не меняется; Drive переконвертирует xlsx.
        drive.files().update(fileId=link.spreadsheet_id, media_body=media).execute()
    else:
        created = drive.files().create(
            body={"name": title, "mimeType": SHEET_MIME},
            media_body=media,
            fields="id,webViewLink",
        ).execute()
        if link is None:
            link = models.SheetLink()
            db.add(link)
        link.spreadsheet_id = created["id"]
        link.url = created.get("webViewLink") or _sheet_url(created["id"])
        link.title = title
    link.last_pushed_at = datetime.utcnow()
    db.commit()
    return {
        "spreadsheet_id": link.spreadsheet_id,
        "url": link.url or _sheet_url(link.spreadsheet_id),
        "title": link.title,
        "last_pushed_at": link.last_pushed_at.isoformat(),
    }


def _to_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return None


def pull_edits(db) -> dict:
    """Читает драйверы с листа «Допущения» и переносит их в профиль бизнеса."""
    link = db.query(models.SheetLink).first()
    if link is None or not link.spreadsheet_id:
        raise google_client.GoogleNotConnected("Нет связанной таблицы — сначала выполните push.")

    sheets = google_client.sheets_service(db)
    cells = list(PULL_CELL_MAP)
    ranges = [f"'{ASSUMPTIONS_SHEET}'!{cell}" for cell in cells]
    resp = sheets.spreadsheets().values().batchGet(
        spreadsheetId=link.spreadsheet_id,
        ranges=ranges,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()

    value_ranges = resp.get("valueRanges", [])
    changed: dict[str, float] = {}
    for cell, value_range in zip(cells, value_ranges):
        rows = value_range.get("values") or []
        raw = rows[0][0] if rows and rows[0] else None
        number = _to_number(raw)
        if number is None:
            continue
        answer_key, factor = PULL_CELL_MAP[cell]
        changed[answer_key] = round(number * factor, 4)

    if not changed:
        return {"updated": {}, "note": "изменений во входах не найдено"}

    profile = db.query(models.BusinessProfile).first()
    if profile is None:
        profile = models.BusinessProfile(industry_code=next(iter(INDUSTRIES)), answers_json="{}")
        db.add(profile)
        db.flush()
    answers = json.loads(profile.answers_json or "{}")
    answers.update(changed)
    profile.answers_json = json.dumps(answers, ensure_ascii=False)
    link.last_pulled_at = datetime.utcnow()
    db.commit()
    return {"updated": changed, "last_pulled_at": link.last_pulled_at.isoformat()}
