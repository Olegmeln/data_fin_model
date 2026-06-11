"""Категоризация операций: правила пользователя → ключевые слова → запасной вариант.

Каскад уровней:
1. Правила, выученные на подтверждениях пользователя (уверенность 0.97).
2. Ключевые слова из шаблона финмодели (уверенность 0.85).
3. ИИ-категоризация — отдельный модуль ``ai.py`` (вызывается из API для остатка).
4. Запасной вариант по направлению платежа (низкая уверенность → «требует подтверждения»).
"""
from sqlalchemy.orm import Session

from .. import models
from ..finmodel.template import DEFAULT_CATEGORIES

# Плоский список встроенных ключевых слов: (ключ, код статьи, тип статьи)
_BUILTIN_KEYWORDS: list[tuple[str, str, str]] = [
    (keyword, category["code"], category["kind"])
    for category in DEFAULT_CATEGORIES
    for keyword in category["keywords"]
]

FALLBACK_INCOME = ("REV_MAIN", 0.5)
FALLBACK_EXPENSE = ("OTHER_EXP", 0.4)


def categorize(
    direction: str,
    counterparty: str | None,
    description: str | None,
    user_rules: list[models.Rule],
) -> tuple[str, float, str]:
    """Возвращает (код статьи, уверенность, источник решения)."""
    counterparty_text = (counterparty or "").lower()
    description_text = (description or "").lower()

    # 1. Правила пользователя
    for rule in user_rules:
        haystack = counterparty_text if rule.field == "counterparty" else description_text
        if rule.pattern and rule.pattern in haystack:
            return rule.category.code, 0.97, "rule"

    # 2. Встроенные ключевые слова (с учётом направления платежа)
    for keyword, code, kind in _BUILTIN_KEYWORDS:
        direction_ok = (
            kind == "transfer"
            or (kind == "income" and direction == "in")
            or (kind == "expense" and direction == "out")
        )
        if direction_ok and (keyword in description_text or keyword in counterparty_text):
            return code, 0.85, "keyword"

    # 3. Запасной вариант
    code, confidence = FALLBACK_INCOME if direction == "in" else FALLBACK_EXPENSE
    return code, confidence, "fallback"


def learn_rule(db: Session, operation: models.Operation) -> models.Rule | None:
    """Создаёт правило «контрагент → статья» после подтверждения пользователем."""
    pattern = (operation.counterparty or "").strip().lower()
    if len(pattern) < 4 or operation.category_id is None:
        return None
    existing = (
        db.query(models.Rule)
        .filter(models.Rule.field == "counterparty", models.Rule.pattern == pattern)
        .first()
    )
    if existing:
        existing.category_id = operation.category_id
        return existing
    rule = models.Rule(field="counterparty", pattern=pattern, category_id=operation.category_id)
    db.add(rule)
    return rule
