"""Парсер банковских выписок в формате PDF (текстовый слой).

Стратегия в два уровня:
  1. Таблицы. pdfplumber извлекает табличные строки; они прогоняются через тот
     же движок распознавания колонок (map_columns) и сборки операций
     (build_operation), что и CSV/XLSX. Заголовок ищется в каждой таблице;
     страницы-продолжения без заголовка используют карту колонок предыдущей.
  2. Плоский текст. Если таблицы не выделились, строки разбираются эвристикой
     «дата … сумма» — запасной вариант для простых макетов.

Отсканированные PDF (без текстового слоя) не поддерживаются — по ним выдаётся
понятная ошибка с предложением включить OCR или загрузить CSV/XLSX.
"""
import io
import re

from .base import (
    ParsedOperation,
    ParserError,
    build_operation,
    map_columns,
    parse_amount,
    parse_date,
)

# Заголовок таблицы ищем в первых строках — как в csv/xlsx-парсерах.
_HEADER_SCAN_ROWS = 15
_OUT_MARKERS = ("спис", "расход", "debit", "оплата", "выдач", "перевод")
_IN_MARKERS = ("поступ", "приход", "credit", "зачис", "пополнен", "возврат")

# «1 234,56» / «1234.56» / «-500,00» / «(500,00)» — одна денежная величина.
_AMOUNT_RE = re.compile(r"[-+(]?\d[\d  ]*[.,]\d{2}\)?")
_DATE_RE = re.compile(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}")


def _clean_row(row) -> list[str]:
    return [("" if cell is None else str(cell).replace("\n", " ").strip()) for cell in row]


def _find_header(rows: list[list[str]]) -> int | None:
    """Индекс строки-заголовка (первая с колонкой «дата»/«date»)."""
    for i, row in enumerate(rows[:_HEADER_SCAN_ROWS]):
        joined = " ".join(row).lower()
        if "дата" in joined or "date" in joined:
            return i
    return None


def _operations_from_tables(tables: list[list[list]]) -> list[ParsedOperation]:
    """Сборка операций из табличных строк с переносом карты колонок между таблицами."""
    operations: list[ParsedOperation] = []
    cols: dict[str, int] | None = None

    for table in tables:
        rows = [_clean_row(r) for r in table if r and any((c or "").strip() for c in r)]
        if not rows:
            continue

        start = 0
        header_index = _find_header(rows)
        if header_index is not None:
            candidate = map_columns(rows[header_index])
            has_amount = "amount" in candidate or "debit" in candidate or "credit" in candidate
            if "date" in candidate and has_amount:
                cols = candidate
                start = header_index + 1

        if cols is None:
            continue  # ждём таблицу, в которой появится распознаваемый заголовок

        for row in rows[start:]:
            op = build_operation(row, cols)
            if op is not None:
                operations.append(op)

    return operations


def _direction_from(line: str, token: str) -> str:
    # Явный знак у суммы приоритетнее ключевых слов.
    t = token.strip()
    if t.startswith("+"):
        return "in"
    if t.startswith("-") or t.startswith("("):
        return "out"
    low = line.lower()
    if any(m in low for m in _IN_MARKERS):
        return "in"
    if any(m in low for m in _OUT_MARKERS):
        return "out"
    return "out"  # консервативно: без явного признака считаем расходом


def _operations_from_text(lines: list[str]) -> list[ParsedOperation]:
    """Запасной разбор плоского текста: строка вида «дата … сумма …»."""
    operations: list[ParsedOperation] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        date_match = _DATE_RE.search(line)
        if not date_match:
            continue
        op_date = parse_date(date_match.group(0))
        if op_date is None:
            continue

        # Сумму ищем только после даты и убираем прочие даты из строки, чтобы
        # не принять «05.06» из «05.06.2026» за денежную величину.
        rest = _DATE_RE.sub(" ", line[date_match.end():])
        amounts = _AMOUNT_RE.findall(rest)
        if not amounts:
            continue
        # Первая денежная величина — сумма операции; последующие (например,
        # остаток по счёту) игнорируются.
        token = amounts[0]
        amount = parse_amount(token)
        if amount is None or amount == 0:
            continue

        direction = _direction_from(line, token)
        middle = rest
        for tok in amounts:
            middle = middle.replace(tok, " ")
        description = " ".join(middle.split())[:255]

        operations.append(ParsedOperation(
            date=op_date,
            amount=abs(amount),
            direction=direction,
            counterparty="",
            description=description,
        ))

    return operations


def parse_pdf(raw: bytes) -> list[ParsedOperation]:
    try:
        import pdfplumber
    except ImportError as exc:  # noqa: F841
        raise ParserError(
            "Разбор PDF требует библиотеку pdfplumber — установите зависимости из requirements.txt."
        ) from exc

    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"Не удалось открыть PDF: {exc}") from exc

    tables: list[list[list]] = []
    text_lines: list[str] = []
    with pdf:
        for page in pdf.pages:
            try:
                for table in (page.extract_tables() or []):
                    tables.append(table)
            except Exception:  # noqa: BLE001 — на «неудобных» страницах падать не должны
                pass
            text_lines.extend((page.extract_text() or "").splitlines())

    operations = _operations_from_tables(tables)
    if not operations:
        operations = _operations_from_text(text_lines)

    if not operations:
        if not any(l.strip() for l in text_lines):
            raise ParserError(
                "В PDF нет текстового слоя (вероятно, скан). Включите OCR или загрузите CSV/XLSX."
            )
        raise ParserError("В PDF не найдено ни одной операции: не распознан табличный формат выписки.")

    return operations
