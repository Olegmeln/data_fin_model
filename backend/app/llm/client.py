"""Единая точка вызова модели: complete / acomplete.

Все агентские навыки ходят сюда, а не в конкретный API вендора. Добавление
нового провайдера = одна функция ниже + запись в _PROVIDERS.
"""
from __future__ import annotations

import json
import logging

import httpx

from ..config import settings
from .base import LLMError, current_provider

logger = logging.getLogger("app.llm")

_TIMEOUT = 120


# ------------------------------------------------------------------ payloads

def _anthropic_request(system: str, user: str, max_tokens: int) -> tuple[str, dict, dict]:
    if not settings.ANTHROPIC_API_KEY:
        raise LLMError("ИИ недоступен: не задан ANTHROPIC_API_KEY")
    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    return settings.ANTHROPIC_API_URL, headers, payload


def _anthropic_parse(data: dict) -> str:
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _openai_request(system: str, user: str, max_tokens: int) -> tuple[str, dict, dict]:
    """OpenAI-совместимый /chat/completions.

    Покрывает OpenAI, OpenRouter, DeepSeek, Together, Groq, Mistral и локальные
    серверы (Ollama, LM Studio, vLLM) — им ключ не нужен, достаточно LLM_BASE_URL.
    """
    base = (settings.llm_base_url or "").rstrip("/")
    if not base:
        raise LLMError("ИИ недоступен: не задан LLM_BASE_URL для провайдера openai")
    headers = {"content-type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
    payload = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    return f"{base}/chat/completions", headers, payload


def _openai_parse(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


_PROVIDERS = {
    "anthropic": (_anthropic_request, _anthropic_parse),
    "openai": (_openai_request, _openai_parse),
}


def _resolve(system: str, user: str, max_tokens: int):
    provider = current_provider()
    if provider == "none":
        raise LLMError(
            "ИИ выключен: задайте LLM_PROVIDER и ключ (LLM_API_KEY / ANTHROPIC_API_KEY) "
            "либо LLM_BASE_URL для локальной модели"
        )
    entry = _PROVIDERS.get(provider)
    if entry is None:
        known = ", ".join(_PROVIDERS)
        raise LLMError(f"неизвестный провайдер LLM: {provider!r} (поддержаны: {known}, none)")
    build, parse = entry
    url, headers, payload = build(system, user, max_tokens)
    return provider, url, headers, payload, parse


def complete(system: str, user: str, max_tokens: int = 8000) -> str:
    """Синхронный вызов модели. Бросает LLMError с человеко-читаемой причиной."""
    provider, url, headers, payload, parse = _resolve(system, user, max_tokens)
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
        return parse(response.json())
    except httpx.HTTPStatusError as exc:
        logger.warning("llm(%s): HTTP %s", provider, exc.response.status_code)
        raise LLMError(f"модель ответила ошибкой HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("llm(%s): %s (%s)", provider, type(exc).__name__, exc)
        raise LLMError(f"сбой обращения к модели: {exc}") from exc


async def acomplete(system: str, user: str, max_tokens: int = 4000) -> str:
    """Асинхронный вызов (используется в категоризации пачками)."""
    provider, url, headers, payload, parse = _resolve(system, user, max_tokens)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return parse(response.json())
    except httpx.HTTPStatusError as exc:
        logger.warning("llm(%s): HTTP %s", provider, exc.response.status_code)
        raise LLMError(f"модель ответила ошибкой HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("llm(%s): %s (%s)", provider, type(exc).__name__, exc)
        raise LLMError(f"сбой обращения к модели: {exc}") from exc
