"""Сборка финансовой модели и данных дашборда из операций."""
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from .. import models


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def build_dashboard(db: Session) -> dict:
    """Агрегирует операции в помесячную модель: KPI, ряды, план/факт по статьям."""
    operations: list[models.Operation] = db.query(models.Operation).all()
    categories: list[models.Category] = db.query(models.Category).order_by(models.Category.id).all()
    plan_values: list[models.PlanValue] = db.query(models.PlanValue).all()

    months = sorted({_month_key(op.date) for op in operations})

    # Помесячные агрегаты
    revenue = defaultdict(float)        # доходные статьи
    expenses = defaultdict(float)       # расходные статьи
    cash_in = defaultdict(float)        # все поступления, кроме внутренних переводов
    cash_out = defaultdict(float)       # все списания, кроме внутренних переводов
    by_category: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for op in operations:
        month = _month_key(op.date)
        amount = float(op.amount)
        kind = op.category.kind if op.category else None

        if kind != "transfer":
            if op.direction == "in":
                cash_in[month] += amount
            else:
                cash_out[month] += amount

        if kind == "income":
            revenue[month] += amount
        elif kind == "expense":
            expenses[month] += amount

        if op.category_id:
            by_category[op.category_id][month] += amount

    # Денежный поток и накопленный остаток
    cash_flow, balance_series = [], []
    balance = 0.0
    for month in months:
        flow = cash_in[month] - cash_out[month]
        balance += flow
        cash_flow.append(round(flow, 2))
        balance_series.append(round(balance, 2))

    # План по статьям и месяцам
    plan_by_category: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for plan in plan_values:
        plan_by_category[plan.category_id][_month_key(plan.month)] += float(plan.amount)

    categories_out = []
    for category in categories:
        fact_monthly = {m: round(by_category[category.id].get(m, 0.0), 2) for m in months}
        plan_monthly = {m: round(plan_by_category[category.id].get(m, 0.0), 2) for m in months}
        fact_total = round(sum(fact_monthly.values()), 2)
        plan_total = round(sum(plan_monthly.values()), 2)
        if fact_total == 0 and plan_total == 0:
            continue
        categories_out.append({
            "code": category.code,
            "name": category.name,
            "kind": category.kind,
            "fact_monthly": fact_monthly,
            "plan_monthly": plan_monthly,
            "fact_total": fact_total,
            "plan_total": plan_total,
            "deviation": round(fact_total - plan_total, 2),
        })

    revenue_total = round(sum(revenue.values()), 2)
    expenses_total = round(sum(expenses.values()), 2)
    net_profit = round(revenue_total - expenses_total, 2)

    top_expenses = sorted(
        (c for c in categories_out if c["kind"] == "expense"),
        key=lambda c: c["fact_total"],
        reverse=True,
    )[:8]

    needs_review = db.query(models.Operation).filter(models.Operation.status == "needs_review").count()
    last_import = db.query(models.ImportLog).order_by(models.ImportLog.created_at.desc()).first()

    return {
        "months": months,
        "kpi": {
            "revenue_total": revenue_total,
            "expenses_total": expenses_total,
            "net_profit": net_profit,
            "margin_pct": round(net_profit / revenue_total * 100, 1) if revenue_total else 0.0,
            "cash_balance": balance_series[-1] if balance_series else 0.0,
            "operations_count": len(operations),
            "needs_review": needs_review,
        },
        "series": {
            "revenue": [round(revenue[m], 2) for m in months],
            "expenses": [round(expenses[m], 2) for m in months],
            "cash_flow": cash_flow,
            "balance": balance_series,
        },
        "categories": categories_out,
        "top_expenses": [
            {"name": c["name"], "total": c["fact_total"]} for c in top_expenses
        ],
        "last_import_at": last_import.created_at.isoformat() if last_import else None,
    }
