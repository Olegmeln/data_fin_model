"""Индустриальные шаблоны: основа интейка и генерации допущений.

Шаблон отрасли задаёт:
- slots — какие данные просить у пользователя (предсказуемый набор упрощает отладку);
- key_categories — статьи, образующие каркас модели отрасли;
- assumption_rules — как из ответов опросника собрать помесячные допущения.

Типы правил (assumption_rules):
- answer:           значение из ответа опросника (answer=<id вопроса>);
- answer_or_pct:    ответ, а если его нет — процент от выручки (value);
- pct_revenue:      процент от выручки месяца (value, опционально min);
- tax:              эффективная ставка налога от выручки по налоговому режиму;
- capex_spread:     сумма из ответа, распределённая на N первых месяцев (months);
- one_time:         сумма из ответа одним платежом в месяце month (индекс от 0);
- annuity:          аннуитетный платёж по кредиту (amount_answer, rate_answer, term месяцев).
"""

INDUSTRIES = [
    {
        "code": "services",
        "name": "Услуги / агентство",
        "description": "Проектная или абонентская выручка, главная статья затрат — команда.",
        "key_categories": ["REV_MAIN", "PAYROLL", "RENT", "MARKETING", "SERVICES", "TAXES", "BANK", "OTHER_EXP"],
        "slots": [
            {"code": "bank_statement", "name": "Банковская выписка за 3–6 месяцев", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "payroll", "name": "Ведомость ФОТ или список команды со ставками", "formats": "XLSX, CSV"},
            {"code": "contracts", "name": "Список договоров / счетов клиентам", "formats": "XLSX, CSV"},
        ],
        "assumption_rules": [
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.40,
             "note": "ФОТ ≈ 40% выручки — типично для услуг"},
            {"category": "MARKETING", "type": "pct_revenue", "value": 0.07, "note": "Маркетинг ~7% выручки"},
            {"category": "SERVICES", "type": "pct_revenue", "value": 0.04, "note": "Сервисы и подрядчики ~4% выручки"},
            {"category": "OTHER_EXP", "type": "pct_revenue", "value": 0.03, "note": "Прочие ~3% выручки"},
        ],
    },
    {
        "code": "retail",
        "name": "Розница / e-commerce",
        "description": "Закупки товара, эквайринг и маркетплейсы, логистика.",
        "key_categories": ["REV_MAIN", "COGS", "PAYROLL", "RENT", "MARKETING", "LOGISTICS", "TAXES", "BANK"],
        "slots": [
            {"code": "bank_statement", "name": "Банковская выписка за 3–6 месяцев", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "sales_report", "name": "Отчёт о продажах (касса / маркетплейс)", "formats": "XLSX, CSV"},
            {"code": "purchases", "name": "Закупки товара у поставщиков", "formats": "XLSX, CSV"},
            {"code": "fees", "name": "Отчёт о комиссиях площадок и эквайринга", "formats": "XLSX, CSV, PDF"},
        ],
        "assumption_rules": [
            {"category": "COGS", "type": "pct_revenue", "value": 0.55, "note": "Закупки ~55% выручки — типично для розницы"},
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.12,
             "note": "ФОТ ≈ 12% выручки"},
            {"category": "MARKETING", "type": "pct_revenue", "value": 0.08, "note": "Реклама ~8% выручки"},
            {"category": "LOGISTICS", "type": "pct_revenue", "value": 0.05, "note": "Доставка ~5% выручки"},
        ],
    },
    {
        "code": "food",
        "name": "Общепит",
        "description": "Фудкост, сменный персонал, сезонность и средний чек.",
        "key_categories": ["REV_MAIN", "COGS", "PAYROLL", "RENT", "MARKETING", "TAXES", "BANK", "OTHER_EXP"],
        "slots": [
            {"code": "bank_statement", "name": "Банковская выписка за 3–6 месяцев", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "cash_report", "name": "Отчёт кассы / эквайринга по дням", "formats": "XLSX, CSV"},
            {"code": "purchases", "name": "Закупки продуктов", "formats": "XLSX, CSV"},
        ],
        "assumption_rules": [
            {"category": "COGS", "type": "pct_revenue", "value": 0.35, "note": "Фудкост ~35% выручки"},
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.25,
             "note": "ФОТ ≈ 25% выручки"},
            {"category": "MARKETING", "type": "pct_revenue", "value": 0.04, "note": "Продвижение ~4% выручки"},
            {"category": "OTHER_EXP", "type": "pct_revenue", "value": 0.05, "note": "Расходники и прочее ~5%"},
        ],
    },
    {
        "code": "saas",
        "name": "Подписки / SaaS",
        "description": "MRR, отток, юнит-экономика, затраты на привлечение.",
        "key_categories": ["REV_MAIN", "PAYROLL", "MARKETING", "SERVICES", "TAXES", "BANK", "OTHER_EXP"],
        "slots": [
            {"code": "bank_statement", "name": "Банковская выписка за 3–6 месяцев", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "billing", "name": "Выгрузка биллинга / подписок (MRR)", "formats": "XLSX, CSV"},
            {"code": "payroll", "name": "Ведомость ФОТ", "formats": "XLSX, CSV"},
        ],
        "assumption_rules": [
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.45,
             "note": "ФОТ ≈ 45% выручки — команда разработки"},
            {"category": "MARKETING", "type": "pct_revenue", "value": 0.15, "note": "Привлечение ~15% выручки"},
            {"category": "SERVICES", "type": "pct_revenue", "value": 0.08, "note": "Инфраструктура и сервисы ~8%"},
        ],
    },
    {
        "code": "production",
        "name": "Производство / опт",
        "description": "Сырьё и себестоимость, склад, отсрочки платежей.",
        "key_categories": ["REV_MAIN", "COGS", "PAYROLL", "RENT", "LOGISTICS", "TAXES", "BANK", "OTHER_EXP"],
        "slots": [
            {"code": "bank_statement", "name": "Банковская выписка за 3–6 месяцев", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "purchases", "name": "Закупки сырья и материалов", "formats": "XLSX, CSV"},
            {"code": "shipments", "name": "Отгрузки и дебиторка", "formats": "XLSX, CSV"},
            {"code": "payroll", "name": "Ведомость ФОТ", "formats": "XLSX, CSV"},
        ],
        "assumption_rules": [
            {"category": "COGS", "type": "pct_revenue", "value": 0.50, "note": "Сырьё ~50% выручки"},
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.18,
             "note": "ФОТ ≈ 18% выручки"},
            {"category": "LOGISTICS", "type": "pct_revenue", "value": 0.06, "note": "Логистика ~6% выручки"},
        ],
    },
    {
        "code": "invest",
        "name": "Инвестиционный проект",
        "description": "CAPEX, кредитное финансирование, выход на плановую выручку. Под подготовку модели для банка или инвестора.",
        "key_categories": [
            "REV_MAIN", "COGS", "PAYROLL", "RENT", "MARKETING", "TAXES", "BANK",
            "CAPEX", "FIN_IN", "FIN_OUT", "OTHER_EXP",
        ],
        "slots": [
            {"code": "capex_estimate", "name": "Смета CAPEX (строительство, оборудование)", "formats": "XLSX, CSV"},
            {"code": "loan_terms", "name": "Кредитный договор или график платежей", "formats": "XLSX, PDF"},
            {"code": "bank_statement", "name": "Банковская выписка (если проект уже идёт)", "formats": "CSV, XLSX, 1С (.txt)"},
            {"code": "payroll", "name": "Штатное расписание / ФОТ", "formats": "XLSX, CSV"},
            {"code": "equipment", "name": "Спецификация оборудования", "formats": "XLSX, CSV"},
        ],
        "assumption_rules": [
            {"category": "COGS", "type": "pct_revenue", "value": 0.30, "note": "Операционная себестоимость ~30% выручки"},
            {"category": "PAYROLL", "type": "answer_or_pct", "answer": "payroll_monthly", "value": 0.20,
             "note": "ФОТ ≈ 20% выручки"},
            {"category": "MARKETING", "type": "pct_revenue", "value": 0.05, "note": "Реклама и открытие ~5%"},
            {"category": "CAPEX", "type": "capex_spread", "answer": "capex_total", "months": 6,
             "note": "Инвестиции, распределённые на первые 6 месяцев"},
            {"category": "FIN_IN", "type": "one_time", "answer": "loan_amount", "month": 0,
             "note": "Получение кредита"},
            {"category": "FIN_OUT", "type": "annuity", "amount_answer": "loan_amount",
             "rate_answer": "loan_rate", "term": 60, "note": "Аннуитетный платёж по кредиту"},
        ],
    },
]

# Базовые правила, общие для всех отраслей (применяются, если отрасль не переопределила статью).
# Инвестиционная рамка действует для любого бизнеса: CAPEX, кредит и аннуитет
# срабатывают у всех, если соответствующие ответы даны в опроснике.
COMMON_RULES = [
    {"category": "REV_MAIN", "type": "answer", "answer": "monthly_revenue", "note": "Целевая месячная выручка из опросника"},
    {"category": "RENT", "type": "answer", "answer": "rent_monthly", "note": "Аренда из опросника"},
    {"category": "TAXES", "type": "tax", "note": "Эффективная ставка по налоговому режиму"},
    {"category": "BANK", "type": "pct_revenue", "value": 0.01, "min": 1500, "note": "Банковское обслуживание ~1% оборота"},
    {"category": "CAPEX", "type": "capex_spread", "answer": "capex_total", "months": 6,
     "note": "Стартовые вложения, распределённые на первые месяцы"},
    {"category": "FIN_IN", "type": "one_time", "answer": "loan_amount", "month": 0,
     "note": "Получение кредита"},
    {"category": "FIN_OUT", "type": "annuity", "amount_answer": "loan_amount",
     "rate_answer": "loan_rate", "term": 60, "note": "Аннуитетный платёж по кредиту"},
]

# Эффективные ставки налога от выручки по режимам (упрощение для стартовых допущений)
TAX_RATES = {
    "usn6": (0.06, "УСН «Доходы» 6%"),
    "usn15": (0.07, "УСН «Доходы минус расходы» — эффективно ~7% от выручки"),
    "osno": (0.12, "ОСНО — эффективно ~12% от выручки (НДС и прибыль)"),
    "npd": (0.05, "НПД / патент — ~5% от выручки"),
    "unknown": (0.07, "Режим не указан — взята средняя ставка 7%"),
}


def get_industry(code: str) -> dict | None:
    for industry in INDUSTRIES:
        if industry["code"] == code:
            return industry
    return None


def industry_public(industry: dict) -> dict:
    """Версия шаблона для выдачи в API (без правил генерации)."""
    return {
        "code": industry["code"],
        "name": industry["name"],
        "description": industry["description"],
        "key_categories": industry["key_categories"],
        "slots": industry["slots"],
    }
