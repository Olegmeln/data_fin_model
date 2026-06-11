"""Определение формата выписки и диспетчеризация парсеров."""
from .base import ParsedOperation, ParserError, decode_bytes
from .csv_parser import parse_csv
from .onec_parser import parse_1c
from .xlsx_parser import parse_xlsx

__all__ = ["ParsedOperation", "ParserError", "parse_statement"]


def parse_statement(filename: str | None, raw: bytes) -> tuple[str, list[ParsedOperation]]:
    """Определяет формат файла и возвращает (формат, список операций)."""
    name = (filename or "").lower()
    head = decode_bytes(raw[:300]).lstrip("\ufeff")

    if name.endswith(".xlsx") or raw[:4] == b"PK\x03\x04":
        return "xlsx", parse_xlsx(raw)
    if "1CClientBankExchange" in head or name.endswith(".txt"):
        return "1c", parse_1c(raw)
    if name.endswith(".csv") or ";" in head or "," in head:
        return "csv", parse_csv(raw)
    raise ParserError("Неизвестный формат файла. Поддерживаются: CSV, XLSX, 1CClientBankExchange (.txt).")
