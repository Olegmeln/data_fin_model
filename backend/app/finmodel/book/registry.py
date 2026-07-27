"""Реестр листов книги (sheet registry).

Книга собирается из зарегистрированных листов; состав и порядок — данные,
а не код. Добавление листа в будущем = регистрация новой записи, движок
и экспорт не переписываются. Коды листов стабильны — это часть контракта
AFM&C (внешний агент адресует лист по коду, не по подписи).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..assumptions_schema import AssumptionSet
from .engine import BookData

SheetBuilder = Callable[["object", AssumptionSet, BookData], None]  # (worksheet, aset, book)


@dataclass(frozen=True)
class SheetSpec:
    code: str            # стабильный код (контракт)
    title: str           # подпись вкладки в книге
    builder_name: str    # имя функции-строителя в excel.py
    default_enabled: bool = True


REGISTRY: tuple[SheetSpec, ...] = (
    SheetSpec("cover", "Обложка", "build_cover"),
    SheetSpec("roadmap", "Дорожная карта", "build_roadmap"),
    SheetSpec("assumptions", "Допущения", "build_assumptions"),
    SheetSpec("cf", "CF", "build_cf"),
    SheetSpec("dashboard", "Дашборд", "build_dashboard"),
    SheetSpec("sales", "План продаж", "build_sales"),
    SheetSpec("production", "Производство", "build_production"),
    SheetSpec("capex", "CAPEX и амортизация", "build_capex_sheet"),
    SheetSpec("opex", "Опер. расходы", "build_opex_sheet"),
    SheetSpec("staff", "ФОТ", "build_staff"),
    SheetSpec("pl", "ПиУ", "build_pl"),
    SheetSpec("balance", "Балансы", "build_balance"),
    SheetSpec("credit", "Кредит", "build_credit"),
    SheetSpec("covenants", "Ковенанты", "build_covenants"),
    SheetSpec("sensitivity", "Чувствительность", "build_sensitivity"),
    # целевая архитектура v1 покрыта полностью; новые листы — через реестр
)


def resolve_sheets(codes: list[str] | None = None) -> list[SheetSpec]:
    """Возвращает упорядоченный состав книги; по умолчанию — все включённые."""
    if codes is None:
        return [s for s in REGISTRY if s.default_enabled]
    by_code = {s.code: s for s in REGISTRY}
    unknown = [c for c in codes if c not in by_code]
    if unknown:
        known = ", ".join(s.code for s in REGISTRY)
        raise ValueError(f"неизвестные листы: {', '.join(unknown)} (в реестре: {known})")
    return [by_code[c] for c in codes]
