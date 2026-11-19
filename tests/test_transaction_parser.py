import pytest

from parsers.transaction_parser import parse_transaction
from utils.errors import InvalidFormatError


class TestValidInput:
    def test_expense_simple(self):
        t = parse_transaction("-300 кофе")
        assert t.amount == 300.0
        assert t.category == "кофе"
        assert t.t_type == "expense"

    def test_income_simple(self):
        t = parse_transaction("+50000 зарплата")
        assert t.amount == 50000.0
        assert t.t_type == "income"

    def test_comma_in_amount(self):
        t = parse_transaction("-150,50 продукты")
        assert t.amount == 150.5

    def test_extra_spaces(self):
        t = parse_transaction("  -300   кофе  ")
        assert t.category == "кофе"

    def test_category_lowercased(self):
        t = parse_transaction("-300 КОФЕ")
        assert t.category == "кофе"


class TestInvalidInput:
    def test_no_sign_raises(self):
        with pytest.raises(InvalidFormatError):
            parse_transaction("2024 был тяжёлый год")

    def test_zero_amount_raises(self):
        with pytest.raises(InvalidFormatError):
            parse_transaction("-0 кофе")

    def test_no_category_raises(self):
        with pytest.raises(InvalidFormatError):
            parse_transaction("-300")

    def test_plain_text_raises(self):
        with pytest.raises(InvalidFormatError):
            parse_transaction("привет")

    def test_empty_raises(self):
        with pytest.raises(InvalidFormatError):
            parse_transaction("")
