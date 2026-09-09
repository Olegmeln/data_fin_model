"""Парсер текстового формата обмена «1С: клиент-банк» (1CClientBankExchange)."""
from .base import ParsedOperation, ParserError, decode_bytes, parse_amount, parse_date


def parse_1c(raw: bytes) -> list[ParsedOperation]:
    text = decode_bytes(raw)
    if "1CClientBankExchange" not in text.split("\n", 1)[0]:
        # Допускаем BOM/пробелы, но сигнатура должна присутствовать в начале файла.
        if "1CClientBankExchange" not in text[:200]:
            raise ParserError("Файл не является выпиской в формате 1CClientBankExchange.")

    own_accounts: set[str] = set()
    documents: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("СекцияДокумент"):
            current = {}
            continue
        if line == "КонецДокумента":
            if current:
                documents.append(current)
            current = None
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if current is not None:
                current[key] = value
            elif key == "РасчСчет" and value:
                own_accounts.add(value)

    operations: list[ParsedOperation] = []
    for doc in documents:
        payer_account = doc.get("ПлательщикСчет") or doc.get("ПлательщикРасчСчет") or ""
        receiver_account = doc.get("ПолучательСчет") or doc.get("ПолучательРасчСчет") or ""

        if payer_account and payer_account in own_accounts:
            direction = "out"
        elif receiver_account and receiver_account in own_accounts:
            direction = "in"
        elif doc.get("ДатаСписано"):
            direction = "out"
        elif doc.get("ДатаПоступило"):
            direction = "in"
        else:
            continue

        op_date = parse_date(doc.get("ДатаСписано") or doc.get("ДатаПоступило") or doc.get("Дата"))
        amount = parse_amount(doc.get("Сумма"))
        if op_date is None or not amount:
            continue

        if direction == "out":
            counterparty = doc.get("Получатель1") or doc.get("Получатель") or ""
        else:
            counterparty = doc.get("Плательщик1") or doc.get("Плательщик") or ""

        operations.append(ParsedOperation(
            date=op_date,
            amount=abs(amount),
            direction=direction,
            counterparty=counterparty.strip()[:255],
            description=doc.get("НазначениеПлатежа", "").strip(),
        ))

    if not operations:
        raise ParserError("В файле 1С не найдено ни одного платёжного документа.")
    return operations
