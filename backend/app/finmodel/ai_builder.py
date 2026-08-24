"""ИИ-сборка модели: языковая модель уточняет допущения по профилю бизнеса.

На вход — ответы опросника и черновик допущений по отраслевым правилам,
на выход — уточнённые помесячные ряды (сезонность, реалистичные доли,
специфика бизнеса) и человекочитаемая сводка логики. При любой ошибке
(нет ключа, таймаут, кривой ответ) сервис тихо остаётся на правилах.
"""
import json
import logging

from ..config import settings
from ..llm import LLMError
from ..llm import complete as llm_complete

logger = logging.getLogger("app.ai.builder")

_SYSTEM = (
    "Ты — опытный финансовый директор. Тебе дают профиль бизнеса (любой бизнес "
    "рассматривается как инвестиционный проект) и черновик помесячных допущений, "
    "собранный по грубым отраслевым правилам. Уточни допущения: добавь сезонность, "
    "сделай доли затрат реалистичными для описанного бизнеса, учти разгон выручки "
    "для запуска, CAPEX и кредит, пользовательские параметры. Суммы — в рублях, "
    "целые числа, без отрицательных значений. Нельзя выдумывать новые статьи: "
    "используй только переданные коды категорий. Длина каждого ряда amounts должна "
    "быть ровно равна горизонту. Если по статье допущение не нужно — не включай её. "
    "Ответ — СТРОГО один JSON-объект без пояснений и markdown: "
    '{"summary": "2–4 предложения о логике модели на русском", '
    '"assumptions": [{"category": "КОД", "amounts": [числа], "note": "краткое обоснование"}]}'
)


def ai_build_assumptions(
    industry: dict, answers: dict, horizon: int, draft_rows: list[dict],
) -> dict | None:
    """Возвращает {"summary": str, "rows": [...]} либо None, если ИИ недоступен."""
    if not settings.ai_enabled:
        return None

    allowed = {
        "REV_MAIN", "REV_OTHER", "COGS", "PAYROLL", "RENT", "MARKETING",
        "SERVICES", "LOGISTICS", "TAXES", "BANK", "OTHER_EXP",
        "CAPEX", "FIN_IN", "FIN_OUT",
    }
    payload = {
        "отрасль": {"код": industry["code"], "название": industry["name"], "описание": industry["description"]},
        "горизонт_месяцев": horizon,
        "ответы_опросника": answers,
        "черновик_допущений_по_правилам": draft_rows,
        "доступные_коды_категорий": sorted(allowed),
    }

    try:
        text = llm_complete(_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=4000).strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except (LLMError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "ai_build_assumptions: %s (%s), industry=%s, horizon=%s",
            type(exc).__name__, exc, industry.get("code"), horizon,
        )
        return None

    rows = []
    for item in data.get("assumptions", []):
        code = item.get("category")
        amounts = item.get("amounts")
        if code not in allowed or not isinstance(amounts, list):
            continue
        cleaned = []
        for value in amounts[:horizon]:
            try:
                cleaned.append(max(round(float(value)), 0))
            except (TypeError, ValueError):
                cleaned.append(0)
        while len(cleaned) < horizon:
            cleaned.append(0)
        if any(cleaned):
            rows.append({"category": code, "amounts": cleaned, "note": (item.get("note") or "ИИ-допущение")[:250]})

    if not rows:
        return None
    summary = (data.get("summary") or "").strip()[:1000]
    return {"summary": summary, "rows": rows}
