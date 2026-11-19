import re
from dataclasses import dataclass

from utils.errors import InvalidFormatError

OPERATION_RE = re.compile(
    r"^\s*(?P<sign>[+-])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)\s+(?P<category>\S.{0,49}?)\s*$"
)


@dataclass
class Transaction:
    amount: float
    t_type: str
    category: str


def parse_transaction(text: str) -> Transaction:
    match = OPERATION_RE.match(text or "")
    if not match:
        raise InvalidFormatError(
            "Не понял операцию. Формат: -500 кофе или +50000 зарплата"
        )

    amount = float(match["amount"].replace(",", "."))
    if amount == 0:
        raise InvalidFormatError("Сумма не может быть нулём")

    t_type = "expense" if match["sign"] == "-" else "income"
    category = match["category"].strip().lower()
    return Transaction(amount=amount, t_type=t_type, category=category)
