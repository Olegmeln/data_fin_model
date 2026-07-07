"""Тесты каскада категоризации: правило пользователя → ключевые слова → фолбэк."""
from types import SimpleNamespace

from app.categorization.rules import FALLBACK_EXPENSE, FALLBACK_INCOME, categorize


def _rule(field: str, pattern: str, code: str):
    """Лёгкий дублёр models.Rule — categorize читает только эти атрибуты."""
    return SimpleNamespace(field=field, pattern=pattern, category=SimpleNamespace(code=code))


def test_user_rule_has_top_priority():
    rules = [_rule("counterparty", "ромашка", "RENT")]
    # Назначение платежа намекает на зарплату, но правило пользователя важнее.
    code, confidence, source = categorize("out", "ООО Ромашка", "зарплата за июнь", rules)
    assert (code, source) == ("RENT", "rule")
    assert confidence > 0.9


def test_keyword_matches_expense():
    code, confidence, source = categorize("out", "ИФНС №7", "налог УСН за 2 квартал", [])
    assert code == "TAXES"
    assert source == "keyword"


def test_keyword_respects_direction():
    # «выручка» — доходное слово; для исходящего платежа оно не должно сработать.
    code, _, source = categorize("out", "", "возврат выручки покупателю", [])
    assert code != "REV_MAIN"


def test_fallback_income():
    code, confidence, source = categorize("in", "Неизвестный контрагент", "платёж без примет", [])
    assert (code, confidence) == FALLBACK_INCOME
    assert source == "fallback"


def test_fallback_expense():
    code, confidence, source = categorize("out", "Неизвестный контрагент", "платёж без примет", [])
    assert (code, confidence) == FALLBACK_EXPENSE
    assert source == "fallback"


def test_none_inputs_do_not_crash():
    code, _, source = categorize("out", None, None, [])
    assert code == FALLBACK_EXPENSE[0]
    assert source == "fallback"
