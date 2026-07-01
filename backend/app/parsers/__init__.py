"""Определение формата выписки и диспетчеризация парсеров."""
import io
import zipfile

from .base import ParsedOperation, ParserError, decode_bytes
from .csv_parser import parse_csv
from .onec_parser import parse_1c
from .pdf_parser import parse_pdf
from .xlsx_parser import parse_xlsx

__all__ = ["ParsedOperation", "ParserError", "parse_statement", "parse_archive"]

# Ограничения ZIP-инжеста (защита от «zip-бомб» и мусора в архиве).
_MAX_ARCHIVE_FILES = 100
_MAX_INNER_BYTES = 25 * 1024 * 1024       # 25 МБ на один файл внутри архива
_MAX_TOTAL_BYTES = 200 * 1024 * 1024      # 200 МБ суммарно после распаковки


def _is_zip(raw: bytes) -> bool:
    return raw[:4] == b"PK\x03\x04"


def _is_ooxml(raw: bytes) -> bool:
    """PK-архив, который на самом деле OOXML-документ (.xlsx/.docx), а не набор выписок."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return "[Content_Types].xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def parse_archive(raw: bytes) -> list[ParsedOperation]:
    """Распаковывает ZIP и прогоняет каждый вложенный файл через parse_statement.

    Операции со всех файлов объединяются; дедупликация выполняется выше по стеку
    (в обработчике загрузки) по хэшу операции, поэтому пересекающиеся выписки
    внутри архива безопасны. Вложенные архивы намеренно не поддерживаются.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ParserError("Не удалось открыть ZIP-архив: файл повреждён или не является ZIP.") from exc

    operations: list[ParsedOperation] = []
    errors: list[str] = []
    total_bytes = 0

    with archive:
        entries = [i for i in archive.infolist() if not i.is_dir()]
        for info in entries[:_MAX_ARCHIVE_FILES]:
            base = info.filename.rsplit("/", 1)[-1]
            low = base.lower()
            if not base or base.startswith(".") or info.filename.startswith("__MACOSX"):
                continue
            if low.endswith(".zip"):
                errors.append(f"{base}: вложенные архивы не поддерживаются")
                continue
            if info.file_size > _MAX_INNER_BYTES:
                errors.append(f"{base}: файл слишком большой")
                continue
            total_bytes += info.file_size
            if total_bytes > _MAX_TOTAL_BYTES:
                errors.append("превышен суммарный размер архива")
                break
            try:
                inner = archive.read(info)
            except Exception:  # noqa: BLE001
                errors.append(f"{base}: не удалось прочитать")
                continue
            try:
                _fmt, ops = parse_statement(base, inner)
            except ParserError as exc:
                errors.append(f"{base}: {exc}")
                continue
            operations.extend(ops)

    if not operations:
        detail = "; ".join(errors) if errors else "подходящих файлов не найдено"
        raise ParserError(f"В ZIP-архиве не найдено операций ({detail}).")
    return operations


def parse_statement(filename: str | None, raw: bytes) -> tuple[str, list[ParsedOperation]]:
    """Определяет формат файла и возвращает (формат, список операций)."""
    name = (filename or "").lower()

    # PDF — по расширению или сигнатуре «%PDF-».
    if name.endswith(".pdf") or raw[:5] == b"%PDF-":
        return "pdf", parse_pdf(raw)

    # ZIP-архив выписок. Важно: .xlsx тоже является PK-архивом, поэтому OOXML
    # исключаем по расширению и по маркеру [Content_Types].xml.
    if name.endswith(".zip") or (_is_zip(raw) and not name.endswith(".xlsx") and not _is_ooxml(raw)):
        return "zip", parse_archive(raw)

    # Excel (.xlsx) — оставшиеся PK-архивы считаем OOXML.
    if name.endswith(".xlsx") or _is_zip(raw):
        return "xlsx", parse_xlsx(raw)

    head = decode_bytes(raw[:300]).lstrip("﻿")
    if "1CClientBankExchange" in head or name.endswith(".txt"):
        return "1c", parse_1c(raw)
    if name.endswith(".csv") or ";" in head or "," in head:
        return "csv", parse_csv(raw)

    raise ParserError(
        "Неизвестный формат файла. Поддерживаются: CSV, XLSX, PDF, ZIP-архив, 1CClientBankExchange (.txt)."
    )
