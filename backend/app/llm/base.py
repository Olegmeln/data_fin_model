"""Базовые типы слоя LLM и выбор провайдера из настроек."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings


class LLMError(Exception):
    """Ошибка обращения к модели, понятная пользователю."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str


def current_provider() -> str:
    """Активный провайдер: явная настройка → автоопределение по ключам → none."""
    explicit = (settings.LLM_PROVIDER or "").strip().lower()
    if explicit:
        return explicit
    if settings.ANTHROPIC_API_KEY:
        return "anthropic"
    if settings.LLM_API_KEY:
        return "openai"
    return "none"


def provider_info() -> dict:
    """Публичное описание слоя ИИ (для /api/health и интерфейса)."""
    provider = current_provider()
    return {
        "provider": provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url if provider != "anthropic" else settings.ANTHROPIC_API_URL,
        "enabled": provider != "none",
        "supported": ["anthropic", "openai", "none"],
    }
