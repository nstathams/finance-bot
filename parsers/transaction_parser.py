import re


def parse_transaction(text: str):
    match = re.match(r'^([+-]?\d+(?:\.\d+)?)\s+(.+)', text.strip())
    if not match:
        return None, "Неверный формат ввода. Пример: -500 кофе или +10000 зарплата."

    raw_amount = match.group(1)
    category = match.group(2).strip()

    category = category.lower()

    try:
        amount = float(raw_amount)
    except ValueError:
        return None, "Ошибка: сумма должна быть числом."

    if amount < 0:
        t_type = "expense"
        amount = abs(amount)
    else:
        t_type = "income"

    return {
        "amount": amount,
        "category": category,
        "type": t_type
    }, None
