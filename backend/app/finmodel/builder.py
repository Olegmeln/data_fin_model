"""Сборка финансовой модели: факт из операций + допущения с происхождением.

Правило слияния: для пары (статья, месяц) значением модели становится факт,
если в этом месяце по статье есть хоть одна операция; иначе — допущение.
Так допущения «вытесняются» фактом по мере загрузки данных.

Любой бизнес рассматривается как инвестиционный проект, поэтому поверх
денежного потока считаются NPV, IRR и срок окупаемости.
"""
import json
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from .. import models
from .industries import get_industry

KIND_ORDER = {"income": 0, "expense": 1, "investing": 2, "financing": 3, "transfer": 9}


def _npv(monthly_rate: float, flows: list[float]) -> float:
    return sum(cf / (1 + monthly_rate) ** (i + 1) for i, cf in enumerate(flows))


def _invest_metrics(flows: list[float], yearly_pct: float) -> dict:
    """NPV (по месячной ставке из годовой), IRR (бисекция), окупаемость."""
    empty = {"npv": None, "irr_pct": None, "payback_months": None, "discount_rate_pct": yearly_pct}
    if not flows or not any(flows):
        return empty
    monthly_rate = (1 + yearly_pct / 100) ** (1 / 12) - 1
    npv = round(_npv(monthly_rate, flows), 2)

    irr_pct = None
    low, high = -0.95, 5.0
    if _npv(low, flows) * _npv(high, flows) < 0:
        for _ in range(100):
            mid = (low + high) / 2
            if _npv(low, flows) * _npv(mid, flows) <= 0:
                high = mid
            else:
                low = mid
        irr_pct = round(((1 + (low + high) / 2) ** 12 - 1) * 100, 1)

    cumulative, payback, was_negative = 0.0, None, False
    for index, cash_flow in enumerate(flows):
        cumulative += cash_flow
        if cumulative < 0:
            was_negative = True
        elif was_negative and payback is None:
            payback = index + 1
    if not was_negative:
        payback = 0  # инвестиционной фазы нет — поток положителен с первого месяца
    return {"npv": npv, "irr_pct": irr_pct, "payback_months": payback, "discount_rate_pct": yearly_pct}


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def build_dashboard(db: Session) -> dict:
    operations: list[models.Operation] = db.query(models.Operation).all()
    categories: list[models.Category] = db.query(models.Category).order_by(models.Category.id).all()
    assumptions: list[models.Assumption] = db.query(models.Assumption).all()
    plan_values: list[models.PlanValue] = db.query(models.PlanValue).all()
    profile: models.BusinessProfile | None = db.query(models.BusinessProfile).first()

    category_by_id = {c.id: c for c in categories}

    # --- Факт по операциям ---------------------------------------------------
    fact_by_cell: dict[tuple[int, str], float] = defaultdict(float)   # (категория, месяц) → сумма
    fact_in: dict[str, float] = defaultdict(float)                    # поступления без переводов
    fact_out: dict[str, float] = defaultdict(float)                   # списания без переводов
    fact_months: set[str] = set()

    for op in operations:
        month = _month_key(op.date)
        amount = float(op.amount)
        fact_months.add(month)
        kind = op.category.kind if op.category else None
        if kind != "transfer":
            if op.direction == "in":
                fact_in[month] += amount
            else:
                fact_out[month] += amount
        if op.category_id:
            fact_by_cell[(op.category_id, month)] += amount

    # --- Допущения ------------------------------------------------------------
    assumption_by_cell: dict[tuple[int, str], models.Assumption] = {}
    for a in assumptions:
        assumption_by_cell[(a.category_id, _month_key(a.month))] = a

    months = sorted(fact_months | {key[1] for key in assumption_by_cell})

    def cell(category_id: int, month: str) -> tuple[float | None, str | None, str | None]:
        """Значение ячейки модели: (сумма, происхождение, источник допущения)."""
        if (category_id, month) in fact_by_cell:
            return round(fact_by_cell[(category_id, month)], 2), "fact", None
        assumption = assumption_by_cell.get((category_id, month))
        if assumption is not None:
            return float(assumption.amount), "assumption", assumption.source
        return None, None, None

    def signed(category: models.Category, value: float) -> float:
        if category.kind == "income" or category.code == "FIN_IN":
            return value
        return -value

    # --- Помесячные ряды (факт + допущения) -----------------------------------
    series_revenue, series_expenses, series_cash_flow, series_balance, series_origin = [], [], [], [], []
    series_project_flow = []  # поток проекта без финансирования — база для NPV/IRR/окупаемости
    balance = 0.0
    for month in months:
        revenue = expenses = 0.0
        assumed_flow = 0.0
        financing_net = 0.0
        uses_assumption = False
        for category in categories:
            if category.kind == "transfer":
                continue
            value, origin, _ = cell(category.id, month)
            if value is None:
                continue
            if origin == "assumption":
                uses_assumption = True
                assumed_flow += signed(category, value)
            if category.kind == "income":
                revenue += value
            elif category.kind == "expense":
                expenses += value
            if category.kind == "financing":
                financing_net += signed(category, value)
        flow = fact_in[month] - fact_out[month] + assumed_flow
        balance += flow
        has_fact = month in fact_months
        series_revenue.append(round(revenue, 2))
        series_expenses.append(round(expenses, 2))
        series_cash_flow.append(round(flow, 2))
        series_project_flow.append(round(flow - financing_net, 2))
        series_balance.append(round(balance, 2))
        series_origin.append("mixed" if has_fact and uses_assumption else ("fact" if has_fact else "assumption"))

    # --- Сетка модели (статьи × месяцы) ----------------------------------------
    industry = get_industry(profile.industry_code) if profile else None
    key_codes = set(industry["key_categories"]) if industry else set()
    used_ids = {key[0] for key in fact_by_cell} | {key[0] for key in assumption_by_cell}

    grid_rows = []
    for category in sorted(categories, key=lambda c: (KIND_ORDER.get(c.kind, 5), c.id)):
        if category.kind == "transfer":
            continue
        if category.code not in key_codes and category.id not in used_ids:
            continue
        cells = []
        fact_total = assumed_total = 0.0
        for month in months:
            value, origin, source = cell(category.id, month)
            cells.append({"v": value, "origin": origin, "src": source})
            if origin == "fact":
                fact_total += value
            elif origin == "assumption":
                assumed_total += value
        grid_rows.append({
            "code": category.code,
            "name": category.name,
            "kind": category.kind,
            "cells": cells,
            "fact_total": round(fact_total, 2),
            "assumed_total": round(assumed_total, 2),
            "value_total": round(fact_total + assumed_total, 2),
        })

    # --- Итоги по статьям, план/факт, структура расходов -----------------------
    plan_by_category: dict[int, float] = defaultdict(float)
    for plan in plan_values:
        plan_by_category[plan.category_id] += float(plan.amount)

    categories_out = []
    for row in grid_rows:
        category = next(c for c in categories if c.code == row["code"])
        plan_total = round(plan_by_category.get(category.id, 0.0), 2)
        categories_out.append({
            "code": row["code"], "name": row["name"], "kind": row["kind"],
            "fact_total": row["fact_total"], "assumed_total": row["assumed_total"],
            "value_total": row["value_total"], "plan_total": plan_total,
            "deviation": round(row["value_total"] - plan_total, 2),
        })

    revenue_total = round(sum(series_revenue), 2)
    expenses_total = round(sum(series_expenses), 2)
    revenue_fact = round(sum(
        v for (cat_id, _), v in fact_by_cell.items()
        if category_by_id[cat_id].kind == "income"
    ), 2)
    expenses_fact = round(sum(
        v for (cat_id, _), v in fact_by_cell.items()
        if category_by_id[cat_id].kind == "expense"
    ), 2)
    net_profit = round(revenue_total - expenses_total, 2)

    top_expenses = sorted(
        (c for c in categories_out if c["kind"] == "expense" and c["value_total"] > 0),
        key=lambda c: c["value_total"], reverse=True,
    )[:8]

    needs_review = db.query(models.Operation).filter(models.Operation.status == "needs_review").count()
    last_import = db.query(models.ImportLog).order_by(models.ImportLog.created_at.desc()).first()

    answers = json.loads(profile.answers_json or "{}") if profile else {}
    try:
        discount_rate_pct = float(str(answers.get("discount_rate") or 20).replace(",", "."))
    except ValueError:
        discount_rate_pct = 20.0
    invest_metrics = _invest_metrics(series_project_flow, discount_rate_pct)

    return {
        "months": months,
        "kpi": {
            "revenue_total": revenue_total,
            "expenses_total": expenses_total,
            "net_profit": net_profit,
            "margin_pct": round(net_profit / revenue_total * 100, 1) if revenue_total else 0.0,
            "cash_balance": series_balance[-1] if series_balance else 0.0,
            "revenue_fact": revenue_fact,
            "expenses_fact": expenses_fact,
            "operations_count": len(operations),
            "needs_review": needs_review,
            "assumptions_count": len(assumptions),
            "fact_through": max(fact_months) if fact_months else None,
            "assumptions_through": max((key[1] for key in assumption_by_cell), default=None),
            "invest": invest_metrics,
        },
        "series": {
            "revenue": series_revenue,
            "expenses": series_expenses,
            "cash_flow": series_cash_flow,
            "balance": series_balance,
            "origin": series_origin,
        },
        "model_grid": {"months": months, "rows": grid_rows},
        "categories": categories_out,
        "top_expenses": [{"name": c["name"], "total": c["value_total"]} for c in top_expenses],
        "industry": ({"code": industry["code"], "name": industry["name"]} if industry else None),
        "last_import_at": last_import.created_at.isoformat() if last_import else None,
    }
