"""Реестр агентских навыков (skill registry) — карта того, что умеет продукт.

Тот же приём, что и sheet registry: состав — данные, а не код. Каждый навык
объявляет роль (M/A/C), этап конвейера, вход/выход, зависимость от LLM,
стоимость и приоритет. Из этого получаются три вещи:

1. **Карта агентов** — /api/agents отдаёт машиночитаемое описание (и людям,
   и внешним агентам: это часть контракта AFM&C).
2. **Маршрутизация задач** — `plan(goal, have)` сортирует навыки топологически
   по зависимостям и отбрасывает то, что уже сделано или недоступно.
3. **Деградация** — если LLM выключен, навыки с requires_llm=True выпадают,
   а их места занимают fallback-навыки (движки правил).

Добавление навыка = одна запись ниже. Ничего в движке менять не нужно.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Role = str  # "M" — modelling, "A" — audit, "C" — controlling


@dataclass(frozen=True)
class Skill:
    code: str                       # стабильный идентификатор (контракт)
    title: str                      # человекочитаемое имя
    role: Role                      # M | A | C
    stage: int                      # этап конвейера 0..9 (docs/Конфигурация…)
    produces: tuple[str, ...]       # какие артефакты появляются
    requires: tuple[str, ...] = ()  # какие артефакты нужны на входе
    requires_llm: bool = False      # нужна ли языковая модель
    fallback: str | None = None     # код навыка-замены без LLM
    cost: int = 1                   # относительная стоимость (токены/время)
    module: str = ""                # где реализован — для навигации по коду
    tags: tuple[str, ...] = field(default_factory=tuple)


# Артефакты конвейера (общий словарь входов/выходов).
# documents, operations, profile, assumptions, completeness,
# validation, book, exports, factuals

REGISTRY: tuple[Skill, ...] = (
    # --- M: сбор и нормализация -------------------------------------------
    Skill("parse_statement", "Парсер выписок (CSV/XLSX/1С)", "M", 2,
          produces=("operations",), requires=("documents",),
          module="app.parsers", tags=("intake", "deterministic")),
    Skill("categorize_rules", "Категоризация правилами и словарём", "M", 2,
          produces=("operations.categorized",), requires=("operations",),
          module="app.categorization.rules", tags=("intake", "deterministic")),
    Skill("categorize_ai", "Категоризация языковой моделью", "M", 2,
          produces=("operations.categorized",), requires=("operations",),
          requires_llm=True, fallback="categorize_rules", cost=3,
          module="app.categorization.ai", tags=("intake", "llm")),
    Skill("extract_text", "Извлечение текста документов", "M", 2,
          produces=("documents.text",), requires=("documents",),
          module="app.finmodel.intake.textract", tags=("intake",)),
    Skill("extract_assumptions", "Извлечение допущений из документов", "M", 3,
          produces=("assumptions",), requires=("documents.text",),
          requires_llm=True, fallback="assumptions_auto", cost=8,
          module="app.finmodel.intake.extractor", tags=("core", "llm")),

    # --- M: формирование допущений ----------------------------------------
    Skill("survey", "Опросник профиля бизнеса", "M", 0,
          produces=("profile",), module="app.finmodel.survey", tags=("core",)),
    Skill("assumptions_from_survey", "Мост опросник → assumptions.v1", "M", 3,
          produces=("assumptions",), requires=("profile",),
          module="app.finmodel.from_survey", tags=("core", "deterministic")),
    Skill("assumptions_auto", "Стартовый профиль допущений", "M", 3,
          produces=("assumptions",), module="app.finmodel.assumptions_schema",
          tags=("core", "deterministic")),
    Skill("assumptions_ai_refine", "Уточнение допущений моделью (сезонность, доли)", "M", 3,
          produces=("assumptions.refined",), requires=("assumptions",),
          requires_llm=True, cost=5, module="app.finmodel.ai_builder", tags=("llm",)),
    Skill("preferences", "Память предпочтений (профиль → дефолты)", "M", 3,
          produces=("assumptions.defaults",), requires=("assumptions",),
          module="app.finmodel.intake.preferences", tags=("memory",)),

    # --- A: проверка -------------------------------------------------------
    Skill("validate", "Валидатор допущений (арифметика, диапазоны)", "A", 6,
          produces=("validation",), requires=("assumptions",),
          module="app.finmodel.intake.validator", tags=("core", "deterministic")),
    Skill("covenants_check", "Проверка ковенант (DSCR/ICR/Долг-EBITDA)", "A", 6,
          produces=("validation.covenants",), requires=("assumptions", "book"),
          module="app.finmodel.book.engine", tags=("bank", "deterministic")),
    Skill("compare_sets", "Кросс-сверка двух наборов (аудит документов)", "A", 6,
          produces=("validation.crossdoc",), requires=("assumptions",),
          module="app.finmodel.intake.validator", tags=("audit", "deterministic")),

    # --- M: сборка и выгрузка ---------------------------------------------
    Skill("build_book", "Сборка книги по реестру листов", "M", 7,
          produces=("book",), requires=("assumptions",),
          module="app.finmodel.book.engine", tags=("core", "deterministic")),
    Skill("export_xlsx", "Экспорт книги в Excel (живые формулы)", "M", 8,
          produces=("exports.xlsx",), requires=("book",),
          module="app.finmodel.book.excel", tags=("core", "deterministic")),
    Skill("sensitivity", "Матрица чувствительности NPV", "M", 7,
          produces=("book.sensitivity",), requires=("assumptions",),
          module="app.finmodel.book.engine", tags=("deterministic",)),

    # --- C: сопровождение --------------------------------------------------
    Skill("plan_fact", "План-факт: вытеснение допущений фактом", "C", 9,
          produces=("factuals",), requires=("operations.categorized", "assumptions"),
          module="app.finmodel.builder", tags=("controlling", "deterministic")),
)


BY_CODE = {s.code: s for s in REGISTRY}


def available(llm_enabled: bool) -> list[Skill]:
    """Навыки, доступные при текущей конфигурации (с учётом деградации без LLM)."""
    return [s for s in REGISTRY if llm_enabled or not s.requires_llm]


def resolve(code: str, llm_enabled: bool) -> Skill | None:
    """Навык или его fallback, если основной недоступен без LLM."""
    skill = BY_CODE.get(code)
    if skill is None:
        return None
    if skill.requires_llm and not llm_enabled:
        return BY_CODE.get(skill.fallback) if skill.fallback else None
    return skill


def plan(goal: str, have: set[str] | None = None, llm_enabled: bool = True) -> list[Skill]:
    """Механизм сортировки задач: какие навыки и в каком порядке выполнить,
    чтобы получить артефакт `goal` из уже имеющихся `have`.

    Обратный проход по зависимостям (что нужно для цели) + топологическая
    сортировка по этапу конвейера и стоимости. Реестр меняется — план
    пересчитывается сам, без правки кода вызывающей стороны.
    """
    have = set(have or ())
    pool = available(llm_enabled)
    chosen: dict[str, Skill] = {}

    def satisfy(artifact: str, depth: int = 0) -> None:
        if artifact in have or depth > 12:
            return
        # производители артефакта: точное совпадение или префикс (book ⊃ book.sensitivity)
        producers = [s for s in pool
                     if artifact in s.produces or any(p.split(".")[0] == artifact for p in s.produces)]
        if not producers:
            return
        # приоритет: детерминированное важнее LLM → меньше недостающих входов
        # → дешевле → раньше по конвейеру. Поэтому наличие готовых артефактов
        # (have) само переключает план на более качественный путь.
        def unmet(s: Skill) -> int:
            return sum(1 for dep in s.requires if dep not in have)

        producers.sort(key=lambda s: (s.requires_llm, unmet(s), s.cost, s.stage))
        best = producers[0]
        if best.code in chosen:
            return
        chosen[best.code] = best
        for dep in best.requires:
            satisfy(dep, depth + 1)

    satisfy(goal)
    return sorted(chosen.values(), key=lambda s: (s.stage, s.cost))


def coverage() -> dict:
    """Карта покрытия: сколько навыков на роль и на этап — видно пробелы."""
    by_role: dict[str, int] = {}
    by_stage: dict[int, int] = {}
    for s in REGISTRY:
        by_role[s.role] = by_role.get(s.role, 0) + 1
        by_stage[s.stage] = by_stage.get(s.stage, 0) + 1
    return {
        "total": len(REGISTRY),
        "by_role": by_role,
        "by_stage": dict(sorted(by_stage.items())),
        "llm_dependent": sum(1 for s in REGISTRY if s.requires_llm),
        "without_fallback": [s.code for s in REGISTRY if s.requires_llm and not s.fallback],
    }


def as_public(skill: Skill) -> dict:
    return {
        "code": skill.code, "title": skill.title, "role": skill.role, "stage": skill.stage,
        "produces": list(skill.produces), "requires": list(skill.requires),
        "requires_llm": skill.requires_llm, "fallback": skill.fallback,
        "cost": skill.cost, "module": skill.module, "tags": list(skill.tags),
    }
