"""Pydantic-схемы тел запросов."""
from typing import Literal

from pydantic import BaseModel, Field

# Месяц строго в формате YYYY-MM (1900–2099).
_MONTH_PATTERN = r"^(19|20)\d{2}-(0[1-9]|1[0-2])$"
# Код статьи: латиница/цифры/подчёркивание, как в справочнике категорий.
_CODE_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,31}$"
# Защита от мусорных чисел (переполнение БД, NaN-подобные крайности).
_MAX_AMOUNT = 1e13


class ConfirmCategoryIn(BaseModel):
    """Подтверждение или смена статьи операции пользователем."""

    category_code: str = Field(..., pattern=_CODE_PATTERN, description="Код статьи из справочника")
    apply_to_similar: bool = Field(
        True, description="Применить статью к другим неподтверждённым операциям этого контрагента",
    )


class PlanItemIn(BaseModel):
    category_code: str = Field(..., pattern=_CODE_PATTERN)
    month: str = Field(..., pattern=_MONTH_PATTERN, description="Месяц в формате YYYY-MM")
    amount: float = Field(..., ge=-_MAX_AMOUNT, le=_MAX_AMOUNT, allow_inf_nan=False)


class PlanUpsertIn(BaseModel):
    items: list[PlanItemIn] = Field(..., min_length=1, max_length=1000)


class SurveyAnswersIn(BaseModel):
    """Ответы интерактивного опросника."""

    answers: dict = Field(..., description="Пары id вопроса → ответ")


class AssumptionItemIn(BaseModel):
    category_code: str = Field(..., pattern=_CODE_PATTERN)
    month: str = Field(..., pattern=_MONTH_PATTERN, description="Месяц в формате YYYY-MM")
    amount: float = Field(..., ge=-_MAX_AMOUNT, le=_MAX_AMOUNT, allow_inf_nan=False)
    note: str | None = Field(None, max_length=500)


class AssumptionUpsertIn(BaseModel):
    items: list[AssumptionItemIn] = Field(..., min_length=1, max_length=1000)


class AssumptionSetPutIn(BaseModel):
    """Сохранение набора допущений (интейк): статус, набор, комментарий."""

    status: Literal["draft", "confirmed"] = "draft"
    assumptions: dict = Field(default_factory=dict)
    comment: str | None = Field(None, max_length=500)
