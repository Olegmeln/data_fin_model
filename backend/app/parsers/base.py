"""Общие утилиты для парсеров банковских выписок."""
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class ParserError(Exception):
    """Ошибка разбора файла выписки (сообщение показывается пользователю)."""


@dataclass
class ParsedOperation:
    date: date
    amount: Decimal  # всегда положительная
    direction: str  # in | out
    counterparty: str
    description: str


def decode_bytes(raw: bytes) -> str:
    """Декодирование с учётом типичных кодировок банковских файлов."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_amount(value) -> Decimal | None:
    """«1 234,56» / «1234.56» / -500 → Decimal. None, если это не число."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-+]", "", s)
    if s in ("", "-", "+", "."):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y", "%Y.%m.%d")


def parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    s = str(value).strip().split(" ")[0].split("T")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# Роль колонки → подстроки-подсказки в заголовке. Порядок важен:
# специфичные роли («расход»/«приход») проверяются раньше общей «суммы».
_HEADER_HINTS = [
    ("date", ("дата", "date")),
    ("debit", ("расход", "списан", "дебет", "debit")),
    ("credit", ("приход", "поступ", "кредит", "credit")),
    ("optype", ("тип", "вид операции", "операция", "дт/кт")),
    ("counterparty", ("контрагент", "плательщик", "получатель", "корреспондент", "partner")),
    ("description", ("назначен", "описан", "основание", "коммент", "purpose", "details")),
    ("amount", ("сумма", "amount", "итого")),
]


def map_columns(headers: list) -> dict[str, int]:
    """Определение ролей колонок по заголовкам. Возвращает {роль: индекс}."""
    lowered = [str(h or "").lower() for h in headers]
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for role, hints in _HEADER_HINTS:
        for idx, header in enumerate(lowered):
            if idx in used or not header:
                continue
            if any(hint in header for hint in hints):
                mapping[role] = idx
                used.add(idx)
                break
    return mapping


_OUT_MARKERS = ("спис", "расход", "debit", "оплата", "выдач")
_IN_MARKERS = ("поступ", "приход", "credit", "зачис", "пополнен")


def build_operation(cells: list, cols: dict[str, int]) -> ParsedOperation | None:
    """Сборка операции из строки таблицы по карте колонок."""

    def cell(role: str):
        idx = cols.get(role)
        if idx is None or idx >= len(cells):
            return None
        return cells[idx]

    op_date = parse_date(cell("date"))
    if op_date is None:
        return None

    amount: Decimal | None = None
    direction: str | None = None

    # Вариант 1: раздельные колонки прихода/расхода
    debit = parse_amount(cell("debit")) if "debit" in cols else None
    credit = parse_amount(cell("credit")) if "credit" in cols else None
    if debit:
        amount, direction = abs(debit), "out"
    elif credit:
        amount, direction = abs(credit), "in"

    # Вариант 2: одна колонка суммы (+ опциональная колонка типа операции)
    if amount is None and "amount" in cols:
        value = parse_amount(cell("amount"))
        if value is None or value == 0:
            return None
        op_type = str(cell("optype") or "").lower()
        if any(marker in op_type for marker in _OUT_MARKERS):
            direction = "out"
        elif any(marker in op_type for marker in _IN_MARKERS):
            direction = "in"
        else:
            direction = "in" if value > 0 else "out"
        amount = abs(value)

    if amount is None or direction is None or amount == 0:
        return None

    return ParsedOperation(
        date=op_date,
        amount=amount,
        direction=direction,
        counterparty=str(cell("counterparty") or "").strip()[:255],
        description=str(cell("description") or "").strip(),
    )
