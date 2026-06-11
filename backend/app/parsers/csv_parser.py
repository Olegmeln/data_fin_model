"""Парсер банковских выписок в формате CSV."""
import csv
import io

from .base import ParsedOperation, ParserError, build_operation, decode_bytes, map_columns


def parse_csv(raw: bytes) -> list[ParsedOperation]:
    text = decode_bytes(raw)
    delimiter = ";" if text.count(";") >= text.count(",") else ","
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in row)]
    if not rows:
        raise ParserError("Файл пуст или не распознан как CSV.")

    # Строка заголовков — первая, где встречается колонка с датой.
    header_index = None
    for i, row in enumerate(rows[:10]):
        joined = " ".join(str(c).lower() for c in row)
        if "дата" in joined or "date" in joined:
            header_index = i
            break
    if header_index is None:
        raise ParserError("Не найдена строка заголовков: ожидается колонка «Дата».")

    cols = map_columns(rows[header_index])
    if "date" not in cols or ("amount" not in cols and "debit" not in cols and "credit" not in cols):
        raise ParserError("Не удалось определить колонки даты и суммы.")

    operations: list[ParsedOperation] = []
    for row in rows[header_index + 1:]:
        op = build_operation(row, cols)
        if op is not None:
            operations.append(op)

    if not operations:
        raise ParserError("В файле не найдено ни одной операции.")
    return operations
