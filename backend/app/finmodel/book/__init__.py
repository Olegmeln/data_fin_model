"""Книга модели: движок рядов и метрик (пункт 5)."""
from .engine import BookData, BookError, apply_overrides, build_book, resolve_scenario

__all__ = ["BookData", "BookError", "apply_overrides", "build_book", "resolve_scenario"]
