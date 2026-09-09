"""Тесты базовых утилит разбора выписок."""
from datetime import date
from decimal import Decimal

import pytest

from app.parsers.base import build_operation, map_columns, parse_amount, parse_date


class TestParseAmount:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("1 234,56", Decimal("1234.56")),
            ("1234.56", Decimal("1234.56")),
            ("-500", Decimal("-500")),
            ("+250,00", Decimal("250.00")),
            ("1\xa0000\xa0000,00", Decimal("1000000.00")),
            ('1 234,56 ₽', Decimal("1234.56")),
            (1500, Decimal("1500")),
            (12.5, Decimal("12.5")),
            (Decimal("7"), Decimal("7")),
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "-", "+", ".", "н/д", "итого"])
    def test_invalid(self, raw):
        assert parse_amount(raw) is None


class TestParseDate:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("15.06.2026", date(2026, 6, 15)),
            ("2026-06-15", date(2026, 6, 15)),
            ("15/06/2026", date(2026, 6, 15)),
            ("15.06.26", date(2026, 6, 15)),
            ("2026-06-15T10:30:00", date(2026, 6, 15)),
            ("15.06.2026 10:30", date(2026, 6, 15)),
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_date(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "не дата", "31.02.2026"])
    def test_invalid(self, raw):
        assert parse_date(raw) is None


class TestMapColumns:
    def test_typical_bank_header(self):
        headers = ["Дата операции", "Списание", "Поступление", "Контрагент", "Назначение платежа"]
        cols = map_columns(headers)
        assert cols["date"] == 0
        assert cols["debit"] == 1
        assert cols["credit"] == 2
        assert cols["counterparty"] == 3
        assert cols["description"] == 4

    def test_single_amount_with_type(self):
        headers = ["Дата", "Сумма", "Тип операции", "Описание"]
        cols = map_columns(headers)
        assert cols["date"] == 0
        assert cols["amount"] == 1
        assert cols["optype"] == 2

    def test_column_used_once(self):
        # Одна и та же колонка не должна получить две роли.
        cols = map_columns(["Дата", "Сумма"])
        indices = list(cols.values())
        assert len(indices) == len(set(indices))


class TestBuildOperation:
    COLS_SPLIT = {"date": 0, "debit": 1, "credit": 2, "counterparty": 3, "description": 4}
    COLS_SINGLE = {"date": 0, "amount": 1, "optype": 2, "description": 3}

    def test_debit_row(self):
        op = build_operation(["01.06.2026", "1 500,00", "", "ООО Ромашка", "Аренда"], self.COLS_SPLIT)
        assert op.direction == "out"
        assert op.amount == Decimal("1500.00")
        assert op.counterparty == "ООО Ромашка"

    def test_credit_row(self):
        op = build_operation(["02.06.2026", "", "10 000", "ООО Клиент", "Оплата по счету"], self.COLS_SPLIT)
        assert op.direction == "in"
        assert op.amount == Decimal("10000")

    def test_single_amount_sign_defines_direction(self):
        op = build_operation(["03.06.2026", "-700", "", "тест"], self.COLS_SINGLE)
        assert op.direction == "out"
        assert op.amount == Decimal("700")

    def test_optype_overrides_sign(self):
        op = build_operation(["03.06.2026", "700", "Списание", "тест"], self.COLS_SINGLE)
        assert op.direction == "out"

    def test_row_without_date_skipped(self):
        assert build_operation(["Итого", "1000", "", ""], self.COLS_SPLIT) is None

    def test_zero_amount_skipped(self):
        assert build_operation(["01.06.2026", "0", "", "", ""], self.COLS_SPLIT) is None
