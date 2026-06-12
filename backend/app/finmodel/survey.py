"""Интерактивный опросник и генерация допущений из ответов.

Вопросы отдаются фронтенду как структура (с условиями показа show_if),
голосовой ввод обрабатывается на клиенте (Web Speech API), сюда приходят
готовые ответы. Из ответов и правил отраслевого шаблона собираются
помесячные допущения на горизонт 12 месяцев.
"""
from datetime import date

from sqlalchemy.orm import Session

from .. import models
from .industries import COMMON_RULES, INDUSTRIES, TAX_RATES, get_industry

HORIZON_MONTHS = 12

QUESTIONS = [
    {
        "id": "industry",
        "type": "select",
        "text": "Чем занимается бизнес?",
        "voice_hint": "Можно ответить голосом: «розница», «услуги», «общепит»…",
        "options": [{"value": i["code"], "label": i["name"]} for i in INDUSTRIES],
    },
    {
        "id": "business_age",
        "type": "select",
        "text": "Бизнес уже работает или только запускается?",
        "options": [
            {"value": "existing", "label": "Уже работает"},
            {"value": "new", "label": "Только запускается"},
        ],
    },
    {
        "id": "monthly_revenue",
        "type": "number",
        "text": "Какая выручка в месяц, в рублях? Примерно.",
        "hint": "Для запускающегося бизнеса — целевая выручка. Например: 800 000",
        "voice_hint": "Скажите сумму, например «восемьсот тысяч» или «1 200 000»",
    },
    {
        "id": "employees",
        "type": "number",
        "text": "Сколько человек в команде, включая вас?",
        "optional": True,
    },
    {
        "id": "payroll_monthly",
        "type": "number",
        "text": "Сколько уходит на зарплаты в месяц?",
        "hint": "Если не знаете точно — пропустите, посчитаю по отраслевой норме",
        "optional": True,
    },
    {
        "id": "rent_monthly",
        "type": "number",
        "text": "Аренда и коммунальные в месяц?",
        "optional": True,
    },
    {
        "id": "tax_mode",
        "type": "select",
        "text": "Какой налоговый режим?",
        "options": [
            {"value": "usn6", "label": "УСН «Доходы» 6%"},
            {"value": "usn15", "label": "УСН «Доходы − расходы» 15%"},
            {"value": "osno", "label": "ОСНО"},
            {"value": "npd", "label": "НПД / патент"},
            {"value": "unknown", "label": "Не знаю"},
        ],
    },
    {
        "id": "capex_total",
        "type": "number",
        "text": "Сколько всего инвестиций нужно (CAPEX)?",
        "hint": "Строительство, оборудование, запуск — общая сумма",
        "show_if": {"industry": "invest"},
    },
    {
        "id": "loan_amount",
        "type": "number",
        "text": "Какую часть закрываете кредитом?",
        "optional": True,
        "show_if": {"industry": "invest"},
    },
    {
        "id": "loan_rate",
        "type": "number",
        "text": "Ставка по кредиту, % годовых?",
        "hint": "Например: 17",
        "optional": True,
        "show_if": {"industry": "invest"},
    },
]


def build_survey() -> dict:
    return {
        "questions": QUESTIONS,
        "industries": [{"code": i["code"], "name": i["name"], "description": i["description"]} for i in INDUSTRIES],
    }


def _to_number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return None


def _add_months(start: date, count: int) -> date:
    month_index = start.month - 1 + count
    return date(start.year + month_index // 12, month_index % 12 + 1, 1)


def _annuity_payment(amount: float, yearly_rate_pct: float, term_months: int) -> float:
    rate = max(yearly_rate_pct, 0) / 100 / 12
    if rate == 0:
        return amount / term_months
    return amount * rate / (1 - (1 + rate) ** -term_months)


def generate_assumptions(db: Session, industry_code: str, answers: dict) -> int:
    """Пересобирает допущения источника 'survey' по ответам опросника.

    Правки пользователя (source='user') имеют приоритет и не затираются.
    Возвращает количество созданных допущений.
    """
    industry = get_industry(industry_code)
    if industry is None:
        raise ValueError(f"Неизвестная отрасль: {industry_code}")

    categories = {c.code: c for c in db.query(models.Category).all()}

    # Старые survey-допущения убираем, пользовательские оставляем как приоритетные
    db.query(models.Assumption).filter(models.Assumption.source == "survey").delete()
    db.flush()
    user_locked = {
        (a.category_id, a.month)
        for a in db.query(models.Assumption.category_id, models.Assumption.month).all()
    }

    start = date.today().replace(day=1)
    months = [_add_months(start, i) for i in range(HORIZON_MONTHS)]

    revenue_target = _to_number(answers.get("monthly_revenue")) or 0.0
    # Запускающийся бизнес выходит на целевую выручку плавно
    ramp = [0.25, 0.50, 0.75] if answers.get("business_age") == "new" else []
    revenue_series = [
        round(revenue_target * (ramp[i] if i < len(ramp) else 1.0), 2)
        for i in range(HORIZON_MONTHS)
    ]

    # Отраслевые правила перекрывают общие по той же статье
    rules_by_category: dict[str, dict] = {}
    for rule in COMMON_RULES + industry["assumption_rules"]:
        rules_by_category[rule["category"]] = rule

    created = 0
    for code, rule in rules_by_category.items():
        category = categories.get(code)
        if category is None:
            continue
        series: list[float | None] = [None] * HORIZON_MONTHS
        note = rule.get("note", "")
        rule_type = rule["type"]

        if rule_type == "answer":
            value = _to_number(answers.get(rule["answer"]))
            if code == "REV_MAIN":
                series = [v if v > 0 else None for v in revenue_series]
            elif value:
                series = [value] * HORIZON_MONTHS
        elif rule_type == "answer_or_pct":
            value = _to_number(answers.get(rule.get("answer", "")))
            if value:
                series = [value] * HORIZON_MONTHS
                note = "Из опросника"
            elif revenue_target:
                series = [round(r * rule["value"], 2) for r in revenue_series]
        elif rule_type == "pct_revenue":
            if revenue_target:
                minimum = rule.get("min", 0)
                series = [round(max(r * rule["value"], minimum), 2) for r in revenue_series]
        elif rule_type == "tax":
            rate, label = TAX_RATES.get(answers.get("tax_mode") or "unknown", TAX_RATES["unknown"])
            if revenue_target:
                series = [round(r * rate, 2) for r in revenue_series]
                note = label
        elif rule_type == "capex_spread":
            total = _to_number(answers.get(rule["answer"]))
            if total:
                spread = rule.get("months", 6)
                portion = round(total / spread, 2)
                series = [portion if i < spread else None for i in range(HORIZON_MONTHS)]
        elif rule_type == "one_time":
            value = _to_number(answers.get(rule["answer"]))
            if value:
                series[rule.get("month", 0)] = value
        elif rule_type == "annuity":
            amount = _to_number(answers.get(rule["amount_answer"]))
            rate_pct = _to_number(answers.get(rule.get("rate_answer", ""))) or 17.0
            if amount:
                payment = round(_annuity_payment(amount, rate_pct, rule.get("term", 60)), 2)
                series = [payment] * HORIZON_MONTHS
                note = f"Аннуитет: {rate_pct:.0f}% годовых, {rule.get('term', 60)} мес."

        for month, value in zip(months, series):
            if not value or value <= 0:
                continue
            if (category.id, month) in user_locked:
                continue
            db.add(models.Assumption(
                category_id=category.id, month=month, amount=value,
                source="survey", note=note or None,
            ))
            created += 1

    db.flush()
    return created
