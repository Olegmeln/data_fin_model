"""Pydantic-схемы тел запросов."""
from pydantic import BaseModel, Field


class ConfirmCategoryIn(BaseModel):
    """Подтверждение или смена статьи операции пользователем."""

    category_code: str = Field(..., description="Код статьи из справочника")
    apply_to_similar: bool = Field(
        True, description="Применить статью к другим неподтверждённым операциям этого контрагента",
    )


class PlanItemIn(BaseModel):
    category_code: str
    month: str = Field(..., description="Месяц в формате YYYY-MM")
    amount: float


class PlanUpsertIn(BaseModel):
    items: list[PlanItemIn]


class SurveyAnswersIn(BaseModel):
    """Ответы интерактивного опросника."""

    answers: dict = Field(..., description="Пары id вопроса → ответ")


class AssumptionItemIn(BaseModel):
    category_code: str
    month: str = Field(..., description="Месяц в формате YYYY-MM")
    amount: float
    note: str | None = None


class AssumptionUpsertIn(BaseModel):
    items: list[AssumptionItemIn]
