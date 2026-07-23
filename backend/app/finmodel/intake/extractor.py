"""Экстрактор допущений: текст документов → AssumptionSet (строго по схеме).

LLM-клиент инъектируется (callable system, user -> str), поэтому модуль
тестируется без сети; боевой клиент — Anthropic API из настроек.
"""
from __future__ import annotations

import json
import re
from typing import Callable

import httpx

from ...config import settings
from ..assumptions_schema import SCHEMA_ID, AssumptionSet, export_json_schema
from .errors import IntakeError
from .textract import ExtractedDoc

LlmFn = Callable[[str, str], str]

_SYSTEM_TEMPLATE = (
    "Ты — Modelling-агент AFM&C. Извлеки из документов допущения финансовой модели "
    "строго по JSON Schema `{schema_id}` (приложена ниже). Правила:\n"
    "1) Ответ — РОВНО один JSON-объект по схеме, без пояснений и markdown.\n"
    "2) Ничего не выдумывай: значение либо есть в документах, либо поле опускается.\n"
    "3) Для каждого извлечённого факта добавь запись в sources: путь поля → "
    "{{method: 'extracted', document: имя файла, locator: страница/лист/раздел, "
    "confidence: 0..1}}. Уверенность честная: прямое число из текста ≈0.9–1.0, "
    "интерпретация ≈0.5–0.8.\n"
    "4) Противоречия между документами и пробелы, мешающие модели, оформляй в "
    "open_questions (severity: blocker — без ответа модель не построить).\n"
    "5) Ставки с изменением во времени задавай расписанием points, а не константой.\n"
    "6) Денежные суммы приводи к миллионам рублей, если в документе не сказано иное; "
    "единицы фиксируй в note источника.\n\n"
    "JSON Schema:\n{schema_json}"
)


def _anthropic_llm(system: str, user: str) -> str:
    """Боевой клиент Anthropic API (используется, если llm не инъектирован)."""
    if not settings.ai_enabled:
        raise IntakeError("ИИ-извлечение недоступно: не задан ANTHROPIC_API_KEY")
    try:
        response = httpx.post(
            settings.ANTHROPIC_API_URL,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.ANTHROPIC_MODEL,
                "max_tokens": 8000,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except httpx.HTTPError as exc:
        raise IntakeError(f"сбой обращения к ИИ: {exc}") from exc


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.DOTALL)
    if match:
        return match.group(1)
    return text


def build_prompt(documents: list[ExtractedDoc]) -> tuple[str, str]:
    """Возвращает (system, user) для вызова LLM — отдельно, чтобы тестировать промпт."""
    system = _SYSTEM_TEMPLATE.format(
        schema_id=SCHEMA_ID,
        schema_json=json.dumps(export_json_schema(), ensure_ascii=False),
    )
    parts = []
    for doc in documents:
        note = " (обрезан по лимиту)" if doc.truncated else ""
        parts.append(f"### Документ: {doc.filename} [{doc.kind}]{note}\n{doc.text}")
    return system, "\n\n".join(parts)


def extract_assumptions(
    documents: list[ExtractedDoc], llm: LlmFn | None = None
) -> AssumptionSet:
    """Извлекает набор допущений из подготовленных документов."""
    if not documents:
        raise IntakeError("не передано ни одного документа")
    system, user = build_prompt(documents)
    raw = (llm or _anthropic_llm)(system, user)

    payload = _strip_fences(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise IntakeError(f"ИИ вернул не-JSON (позиция {exc.pos}): {payload[:200]!r}") from exc

    try:
        return AssumptionSet.from_json(data)
    except Exception as exc:
        raise IntakeError(f"ответ ИИ не прошёл схему {SCHEMA_ID}: {exc}") from exc
