"""Экспорт книги в Excel по реестру листов.

Конвенции финмоделей: синий шрифт — вводы, чёрный — формулы, зелёный —
ссылки на другой лист; итоги — живыми формулами (SUM/ссылки), а не
зашитыми числами. Помесячные ряды операционного движка пишутся
значениями с явной пометкой источника «движок AFM&C» (формульная
трассировка рядов — этап 5.3).
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..assumptions_schema import AssumptionSet
from .engine import BookData
from .registry import SheetSpec, resolve_sheets

FONT = "Arial"
INPUT = Font(name=FONT, color="0000FF")            # синий — ввод
FORMULA = Font(name=FONT, color="000000")          # чёрный — формула
LINK = Font(name=FONT, color="008000")             # зелёный — ссылка на лист
HEADER = Font(name=FONT, bold=True)
TITLE = Font(name=FONT, bold=True, size=14)
NOTE = Font(name=FONT, italic=True, size=9, color="666666")
KEY_FILL = PatternFill("solid", start_color="FFFF00")
MONEY = "#,##0.0;(#,##0.0);-"
PCT = "0.0%"


def _row(ws, r, label, values, font=FORMULA, fmt=MONEY, start_col=2):
    ws.cell(row=r, column=1, value=label).font = HEADER
    for i, v in enumerate(values):
        cell = ws.cell(row=r, column=start_col + i, value=v)
        cell.font = font
        cell.number_format = fmt


def build_cover(ws, aset: AssumptionSet, book: BookData) -> None:
    ws.cell(row=2, column=2, value=aset.profile.name).font = TITLE
    rows = [
        ("Тип проекта", aset.profile.project_type),
        ("Отрасль", aset.profile.industry),
        ("Локация", aset.profile.location),
        ("Валюта", aset.profile.currency),
        ("Горизонт, лет", aset.profile.horizon_years),
        ("Старт модели", aset.profile.model_start.isoformat() if aset.profile.model_start else None),
    ]
    r = 4
    for label, value in rows:
        if value is None:
            continue
        ws.cell(row=r, column=2, value=label).font = HEADER
        ws.cell(row=r, column=3, value=value).font = INPUT
        r += 1
    r += 1
    for q in aset.open_questions:
        ws.cell(row=r, column=2, value=("⛔ " if q.severity == "blocker" else "⚠ ") + q.question).font = NOTE
        r += 1
    ws.cell(row=r + 1, column=2,
            value="Книга собрана по схеме finmodel.assumptions.v1 (AFM&C)").font = NOTE
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 46


def build_assumptions(ws, aset: AssumptionSet, book: BookData) -> None:
    ws.cell(row=1, column=1, value="Допущения (вводы — синим; жёлтая заливка — ключевые)").font = HEADER
    r = 3
    ws.cell(row=r, column=1, value="Продукты").font = HEADER
    r += 1
    for p in aset.products:
        ws.cell(row=r, column=1, value=p.name).font = HEADER
        c_price = ws.cell(row=r, column=2, value=p.start_price)
        c_price.font = INPUT
        c_price.fill = KEY_FILL
        c_price.number_format = MONEY
        c_vol = ws.cell(row=r, column=3, value=p.start_volume)
        c_vol.font = INPUT
        c_vol.number_format = "#,##0"
        ws.cell(row=r, column=4, value=f"разгон {p.ramp_up_months or 0} мес").font = NOTE
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Финансирование").font = HEADER
    r += 1
    ws.cell(row=r, column=1, value="Собственные средства")
    equity = ws.cell(row=r, column=2, value=aset.financing.equity_amount)
    equity.font = INPUT
    equity.number_format = MONEY
    r += 1
    for f in aset.financing.facilities:
        ws.cell(row=r, column=1, value=f"Кредит: {f.name}").font = HEADER
        amount = ws.cell(row=r, column=2, value=f.amount)
        amount.font = INPUT
        amount.fill = KEY_FILL
        amount.number_format = MONEY
        ws.cell(row=r, column=3, value=f"{f.term_months} мес, грейс {f.grace_months}").font = NOTE
        rate = ws.cell(row=r, column=4, value=f.rate.points[0].value_pct / 100)
        rate.font = INPUT
        rate.number_format = PCT
        if f.rate_formula:
            ws.cell(row=r, column=5, value=f.rate_formula).font = NOTE
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Оценка").font = HEADER
    r += 1
    ws.cell(row=r, column=1, value="Ставка дисконтирования")
    disc = ws.cell(row=r, column=2, value=aset.valuation.discount_rate_pct / 100)
    disc.font = INPUT
    disc.fill = KEY_FILL
    disc.number_format = PCT
    if aset.valuation.reinvest_roa_pct is not None:
        r += 1
        ws.cell(row=r, column=1, value="ROA реинвестирования (MIRR)")
        roa = ws.cell(row=r, column=2, value=aset.valuation.reinvest_roa_pct / 100)
        roa.font = INPUT
        roa.number_format = PCT
    r += 2
    for path, src in aset.sources.items():
        ws.cell(row=r, column=1,
                value=f"{path}: {src.method.value}"
                      + (f", {src.document}" if src.document else "")
                      + (f" ({src.locator})" if src.locator else "")
                      + f", conf={src.confidence:.2f}").font = NOTE
        r += 1
    ws.column_dimensions["A"].width = 30


def build_cf(ws, aset: AssumptionSet, book: BookData) -> None:
    n = len(book.months)
    ws.cell(row=1, column=1, value="Денежный поток, помесячно").font = HEADER
    ws.cell(row=1, column=2 + n,
            value="Ряды 3–8 — расчёт движка AFM&C по Допущениям; итоги ниже — формулы").font = NOTE
    _row(ws, 2, "Месяц", [m.strftime("%Y-%m") for m in book.months], font=HEADER, fmt="@")
    _row(ws, 3, "Выручка", book.revenue)
    _row(ws, 4, "OPEX", book.opex)
    _row(ws, 5, "CAPEX", book.capex)
    _row(ws, 6, "Транш кредита", book.debt_draw)
    _row(ws, 7, "Проценты", book.interest)
    _row(ws, 8, "Погашение тела", book.principal)
    for i in range(n):
        col = get_column_letter(2 + i)
        ebitda = ws.cell(row=9, column=2 + i, value=f"={col}3-{col}4")
        ebitda.font = FORMULA
        ebitda.number_format = MONEY
        net = ws.cell(row=10, column=2 + i, value=f"={col}9-{col}5+{col}6-{col}7-{col}8")
        net.font = FORMULA
        net.number_format = MONEY
        prev = get_column_letter(1 + i)
        cumulative = ws.cell(
            row=11, column=2 + i,
            value=f"={col}10" if i == 0 else f"={prev}11+{col}10")
        cumulative.font = FORMULA
        cumulative.number_format = MONEY
    ws.cell(row=9, column=1, value="EBITDA (формула)").font = HEADER
    ws.cell(row=10, column=1, value="Чистый поток (формула)").font = HEADER
    ws.cell(row=11, column=1, value="Кумулятив (формула)").font = HEADER
    ws.column_dimensions["A"].width = 24
    ws.freeze_panes = "B3"


def build_dashboard(ws, aset: AssumptionSet, book: BookData) -> None:
    n = len(book.months)
    last = get_column_letter(1 + n)
    ws.cell(row=1, column=1, value="Дашборд").font = TITLE
    r = 3
    agg = [
        ("Выручка, итого", f"=SUM(CF!B3:{last}3)"),
        ("OPEX, итого", f"=SUM(CF!B4:{last}4)"),
        ("CAPEX, итого", f"=SUM(CF!B5:{last}5)"),
        ("EBITDA, итого", f"=SUM(CF!B9:{last}9)"),
        ("Чистый поток, итого", f"=SUM(CF!B10:{last}10)"),
    ]
    for label, formula in agg:
        ws.cell(row=r, column=1, value=label).font = HEADER
        cell = ws.cell(row=r, column=2, value=formula)
        cell.font = LINK
        cell.number_format = MONEY
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Инвестиционные метрики (движок AFM&C: FCFF, MIRR по ROA)").font = NOTE
    r += 1
    metric_labels = [
        ("NPV", book.metrics["npv"], MONEY),
        ("IRR", (book.metrics["irr_pct"] or 0) / 100, PCT),
        ("MIRR", (book.metrics["mirr_pct"] / 100) if book.metrics["mirr_pct"] is not None else None, PCT),
        ("Окупаемость, мес", book.metrics["payback_months"], "#,##0"),
        ("DSCR min", book.metrics["dscr_min"], "0.00"),
    ]
    for label, value, fmt in metric_labels:
        ws.cell(row=r, column=1, value=label).font = HEADER
        cell = ws.cell(row=r, column=2, value=value)
        cell.font = FORMULA
        cell.number_format = fmt
        r += 1
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 18


def build_credit(ws, aset: AssumptionSet, book: BookData) -> None:
    n = len(book.months)
    _row(ws, 2, "Месяц", [m.strftime("%Y-%m") for m in book.months], font=HEADER, fmt="@")
    _row(ws, 3, "Проценты", book.interest)
    _row(ws, 4, "Погашение тела", book.principal)
    for i in range(n):
        col = get_column_letter(2 + i)
        cell = ws.cell(row=5, column=2 + i, value=f"={col}3+{col}4")
        cell.font = FORMULA
        cell.number_format = MONEY
    ws.cell(row=5, column=1, value="Обслуживание долга (формула)").font = HEADER
    _row(ws, 6, "Остаток долга", book.debt_outstanding)
    total = ws.cell(row=8, column=2, value=f"=SUM(B4:{get_column_letter(1 + n)}4)")
    total.font = FORMULA
    total.number_format = MONEY
    ws.cell(row=8, column=1, value="Погашено всего (формула)").font = HEADER
    ws.column_dimensions["A"].width = 26
    ws.freeze_panes = "B3"


_BUILDERS = {
    "build_cover": build_cover,
    "build_assumptions": build_assumptions,
    "build_cf": build_cf,
    "build_dashboard": build_dashboard,
    "build_credit": build_credit,
}


def export_book_xlsx(aset: AssumptionSet, book: BookData,
                     sheet_codes: list[str] | None = None) -> bytes:
    """Собирает книгу по реестру и возвращает xlsx-байты."""
    specs: list[SheetSpec] = resolve_sheets(sheet_codes)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for spec in specs:
        ws = workbook.create_sheet(title=spec.title)
        _BUILDERS[spec.builder_name](ws, aset, book)
        for row in ws.iter_rows():
            for cell in row:
                if cell.font is None or cell.font.name != FONT:
                    cell.font = Font(name=FONT,
                                     bold=cell.font.bold if cell.font else False,
                                     italic=cell.font.italic if cell.font else False,
                                     size=cell.font.size if cell.font else 11,
                                     color=cell.font.color if cell.font else None)
        ws.sheet_view.showGridLines = False
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
