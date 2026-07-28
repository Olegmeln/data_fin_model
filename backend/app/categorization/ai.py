"""ИИ-категоризация операций через Anthropic API (Claude).

Модуль необязательный: без ключа API сервис работает на движке правил.
Запрашиваем строгий JSON и аккуратно разбираем ответ; любая ошибка сети или
формата приводит к пустому результату — операции просто останутся в статусе
«требует подтверждения».
"""
import json
import logging

from ..config import settings
from ..llm import LLMError, acomplete

logger = logging.getLogger("app.ai.categorize")

_BATCH_SIZE = 40

_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик. Тебе дают банковские операции компании и список "
    "статей финансовой модели. Определи статью для каждой операции. "
    "Отвечай ТОЛЬКО валидным JSON-массивом без пояснений и без markdown: "
    '[{"id": <id операции>, "code": "<код статьи>", "confidence": <число 0..1>}]. '
    "Используй только коды из переданного списка статей. Если не уверен — "
    "ставь confidence ниже 0.6."
)


def _build_user_prompt(items: list[dict], categories: list[dict]) -> str:
    categories_block = "\n".join(
        f'- {c["code"]}: {c["name"]} ({"доход" if c["kind"] == "income" else "расход" if c["kind"] == "expense" else "перевод"})'
        for c in categories
    )
    operations_block = json.dumps(items, ensure_ascii=False)
    return (
        f"Статьи финансовой модели:\n{categories_block}\n\n"
        f"Операции (direction: in — поступление, out — списание):\n{operations_block}"
    )


async def ai_categorize(
    items: list[dict],
    categories: list[dict],
) -> dict[int, tuple[str, float]]:
    """items: [{"id", "direction", "amount", "counterparty", "description"}, ...]

    Возвращает {id: (код статьи, уверенность)} для распознанных операций.
    """
    if not settings.ai_enabled or not items:
        return {}

    valid_codes = {c["code"] for c in categories}
    results: dict[int, tuple[str, float]] = {}

    for start in range(0, len(items), _BATCH_SIZE):
        batch = items[start:start + _BATCH_SIZE]
        try:
            # Провайдер выбирается слоем app.llm — навык не знает вендора.
            text = await acomplete(_SYSTEM_PROMPT, _build_user_prompt(batch, categories), max_tokens=4000)
            text = text.replace("```json", "").replace("```", "").strip()
            for row in json.loads(text):
                op_id = int(row.get("id"))
                code = str(row.get("code", ""))
                confidence = float(row.get("confidence", 0))
                if code in valid_codes:
                    results[op_id] = (code, max(0.0, min(confidence, 1.0)))
        except (LLMError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            # Деградация без ИИ допустима, но причина должна попасть в логи
            # (без содержимого операций — только метаданные).
            logger.warning(
                "ai_categorize: %s (%s), batch_start=%s, batch_size=%s",
                type(exc).__name__, exc, start, len(batch),
            )
            continue

    return results
