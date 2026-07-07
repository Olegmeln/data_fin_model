"""Тесты определения формата и разбора демо-выписок из samples/."""
import pytest

from app.parsers import ParserError, parse_statement


def test_demo_csv_parsed(sample_csv):
    fmt, operations = parse_statement("выписка_demo_июнь_2026.csv", sample_csv)
    assert fmt == "csv"
    assert len(operations) > 0
    for op in operations:
        assert op.direction in ("in", "out")
        assert op.amount > 0
        assert op.date is not None


def test_demo_1c_parsed(sample_1c):
    fmt, operations = parse_statement("statement_1c_demo.txt", sample_1c)
    assert fmt == "1c"
    assert len(operations) > 0
    assert {op.direction for op in operations} <= {"in", "out"}


def test_format_detected_without_filename(sample_csv):
    fmt, operations = parse_statement(None, sample_csv)
    assert fmt == "csv"
    assert operations


def test_unknown_format_raises():
    with pytest.raises(ParserError):
        parse_statement("data.bin", b"\x00\x01\x02\x03 no structure here")


def test_empty_csv_raises():
    with pytest.raises(ParserError):
        parse_statement("empty.csv", b"")


def test_csv_without_date_header_raises():
    raw = "колонка1;колонка2\nзначение;123\n".encode("utf-8")
    with pytest.raises(ParserError):
        parse_statement("bad.csv", raw)


def test_cp1251_encoding_supported():
    raw = "Дата;Сумма;Назначение\n01.06.2026;-100;Аренда офиса\n".encode("cp1251")
    fmt, operations = parse_statement("win.csv", raw)
    assert fmt == "csv"
    assert operations[0].direction == "out"
    assert operations[0].description == "Аренда офиса"
