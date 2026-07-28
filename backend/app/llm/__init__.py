"""Слой доступа к LLM: один интерфейс, много провайдеров.

Продукт агентный (AFM&C), поэтому модель-исполнитель — сменная деталь, а не
фундамент. Навыки агентов (промпты, схемы, валидаторы) живут выше этого слоя
и не знают, какой вендор отвечает.

Контракт: `complete(system, user, max_tokens) -> str` (и async-вариант).
Провайдер выбирается настройками; неизвестный ключ → LLMError с понятным текстом.

Поддержано:
- anthropic       — Anthropic Messages API (Claude);
- openai          — любой сервис с OpenAI-совместимым /chat/completions:
                    OpenAI, OpenRouter, DeepSeek, Together, Groq, Mistral,
                    локальные Ollama/LM Studio/vLLM (через LLM_BASE_URL);
- none            — ИИ выключен, работают движки правил.
"""
from .base import LLMError, LLMResult, current_provider, provider_info
from .client import acomplete, complete

__all__ = ["LLMError", "LLMResult", "complete", "acomplete", "provider_info", "current_provider"]
