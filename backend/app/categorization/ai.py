"""ИИ-категоризация операций через Anthropic API (Claude).

Модуль необязательный: без ключа API сервис работает на движке правил.
Запрашиваем строгий JSON и аккуратно разбираем ответ; любая ошибка сети или
формата приводит к пустому результату — операции просто останутся в статусе
«требует подтверждения».
"""
import json
import logging

import httpx

from ..config import settings

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

    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=90) as client:
        for start in range(0, len(items), _BATCH_SIZE):
            batch = items[start:start + _BATCH_SIZE]
            payload = {
                "model": settings.ANTHROPIC_MODEL,
                "max_tokens": 4000,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": _build_user_prompt(batch, categories)}],
            }
            try:
                response = await client.post(settings.ANTHROPIC_API_URL, headers=headers, json=payload)
                response.raise_for_status()
                text = response.json()["content"][0]["text"]
                text = text.replace("```json", "").replace("```", "").strip()
                for row in json.loads(text):
                    op_id = int(row.get("id"))
                    code = str(row.get("code", ""))
                    confidence = float(row.get("confidence", 0))
                    if code in valid_codes:
                        results[op_id] = (code, max(0.0, min(confidence, 1.0)))
            except httpx.HTTPStatusError as exc:
                # Деградация без ИИ допустима, но причина должна попасть в логи
                # (без содержимого операций — только метаданные).
                logger.warning(
                    "ai_categorize: HTTP %s от API, batch_start=%s, batch_size=%s",
                    exc.response.status_code, start, len(batch),
                )
                continue
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "ai_categorize: %s (%s), batch_start=%s, batch_size=%s",
                    type(exc).__name__, exc, start, len(batch),
                )
                continue

    return results
