"""Стартовые драйверы модели из профиля бизнеса (ответы опросника).

Единый источник правды для генератора Excel-модели и для Google-синхронизации.
"""
import json

from .industries import TAX_RATES


def _num(answers: dict, key: str):
    value = answers.get(key)
    try:
        return float(str(value).replace(" ", "").replace(",", ".")) if value not in (None, "") else None
    except ValueError:
        return None


def drivers_from_profile(profile) -> dict:
    """Словарь драйверов для build_model_workbook. None-профиль → пустой словарь."""
    if profile is None:
        return {}
    answers = json.loads(profile.answers_json or "{}")
    mapping = {
        "base_revenue": _num(answers, "monthly_revenue"),
        "payroll": _num(answers, "payroll_monthly"),
        "rent": _num(answers, "rent_monthly"),
        "capex_total": _num(answers, "capex_total"),
        "loan_amount": _num(answers, "loan_amount"),
    }
    drivers = {key: value for key, value in mapping.items() if value}
    if _num(answers, "loan_rate"):
        drivers["loan_rate"] = _num(answers, "loan_rate") / 100
    if _num(answers, "discount_rate"):
        drivers["discount_rate"] = _num(answers, "discount_rate") / 100
    if answers.get("tax_mode") in TAX_RATES:
        drivers["tax_rate"] = TAX_RATES[answers["tax_mode"]][0]
    if answers.get("business_age") == "new":
        drivers["ramp_months"] = 4
    return drivers
