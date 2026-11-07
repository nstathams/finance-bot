import aiosqlite
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional
from dateutil.relativedelta import relativedelta

DB_NAME = "finance.db"

def get_day_start_end(date: datetime) -> tuple[datetime, datetime]:
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def get_month_start_end(date: datetime) -> tuple[datetime, datetime]:
    start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start + relativedelta(months=1) - timedelta(microseconds=1)

    return start, end


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # 0. Таблица регулярных платежей (НОВАЯ)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recurring_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                category TEXT,
                type TEXT,
                freq TEXT,
                action TEXT,
                next_run TEXT
            )
        """)
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Таблица users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0
            )
        """)

        # 2. Таблица transactions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                category TEXT,
                type TEXT,
                date TEXT
            )
        """)

        # 3. Таблица category_limits
        await db.execute("""
            CREATE TABLE IF NOT EXISTS category_limits (
                user_id INTEGER,
                category TEXT,
                limit_amount REAL,
                reset_date TEXT,
                PRIMARY KEY (user_id, category)
            )
        """)

        # 4. Миграция: добавляем reset_date, если её нет
        try:
            await db.execute("ALTER TABLE category_limits ADD COLUMN reset_date TEXT")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise e

        # 5. Инициализируем reset_date для старых записей
        today_str = datetime.now().strftime("%Y-%m-%d")
        await db.execute(
            "UPDATE category_limits SET reset_date = ? WHERE reset_date IS NULL",
            (today_str,)
        )

        await db.commit()


async def export_transactions_to_csv_rows(user_id: int) -> List[List[str]]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT date, category, type, amount 
            FROM transactions 
            WHERE user_id = ? 
            ORDER BY date DESC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

    csv_data = [["Дата", "Категория", "Тип операции", "Сумма (₽)"]]

    for row in rows:
        raw_date, category, t_type, amount = row

        try:
            parsed_dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
            nice_date = parsed_dt.strftime("%d.%m.%Y")
        except ValueError:
            nice_date = raw_date

        nice_type = "Пополнение" if t_type == "income" else "Трата"
        nice_amount = f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " ₽"

        csv_data.append([nice_date, category.capitalize(), nice_type, nice_amount])

    return csv_data



async def get_period_stats(user_id: int, start_date: datetime | date, end_date: datetime | date) -> Dict[str, Any]:
    # 1. Нормализация дат
    if isinstance(start_date, date) and not isinstance(start_date, datetime):
        start_date = datetime.combine(start_date, datetime.min.time())
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_date = datetime.combine(end_date, datetime.max.time())

    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row

        start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")

        # 3. Используем эти строки в запросе
        cursor = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS total_income
            FROM transactions
            WHERE user_id = ? AND date BETWEEN ? AND ?
            """,
            (user_id, start_str, end_str)
        )
        row = await cursor.fetchone()
        total_expense = float(row["total_expense"] or 0.0)
        total_income = float(row["total_income"] or 0.0)

        # Детализация по категориям (только расходы)
        cursor = await db.execute(
            """
            SELECT category, SUM(amount) AS amount
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND date BETWEEN ? AND ?
            GROUP BY category
            ORDER BY amount DESC
            """,
            (user_id, start_date.isoformat(), end_date.isoformat())
        )
        categories = [
            {"category": r["category"], "amount": float(r["amount"] or 0.0)}
            for r in await cursor.fetchall()
        ]

    return {
        "total_expense": total_expense,
        "total_income": total_income,
        "categories": categories,
    }


async def add_transaction(user_id: int, amount: float, category: str, t_type: str):
    category_lower = category.lower()
    today = datetime.now()
    current_month_str = today.strftime("%Y-%m")

    async with aiosqlite.connect(DB_NAME) as db:
        try:
            # 1. Вставляем транзакцию
            await db.execute(
                "INSERT INTO transactions (user_id, amount, category, type, date) VALUES (?, ?, ?, ?, ?)",
                (user_id, amount, category_lower, t_type, today.strftime("%Y-%m-%d %H:%M:%S"))
            )

            # 2. Обновляем баланс
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)",
                (user_id,)
            )
            sign = 1 if t_type == 'income' else -1
            await db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (amount * sign, user_id)
            )

            warning = None

            # 3. Проверка лимита
            if t_type == 'expense':
                cursor_limit = await db.execute(
                    "SELECT limit_amount, reset_date FROM category_limits WHERE user_id = ? AND category = ?",
                    (user_id, category_lower)
                )
                row_limit = await cursor_limit.fetchone()

                if row_limit:
                    limit_value = row_limit[0]
                    reset_date_str = row_limit[1] or today.strftime("%Y-%m-%d")
                    reset_month_str = reset_date_str[:7]

                    # Если месяц сменился, обновляем дату сброса
                    if current_month_str > reset_month_str:
                        await db.execute(
                            "UPDATE category_limits SET reset_date = ? WHERE user_id = ? AND category = ?",
                            (today.strftime("%Y-%m-%d"), user_id, category_lower)
                        )

                    # Сумма трат за текущий месяц
                    cursor_sum = await db.execute(
                        """
                        SELECT COALESCE(SUM(amount), 0) FROM transactions
                        WHERE user_id = ?
                          AND category = ?
                          AND type = 'expense'
                          AND date >= ?
                        """,
                        (user_id, category_lower, current_month_str + "-01 00:00:00")
                    )
                    total_spent_row = await cursor_sum.fetchone()
                    total_spent = float(total_spent_row[0])

                    if total_spent > limit_value:
                        excess = total_spent - limit_value
                        warning = (
                            f"⚠️ **ПРЕВЫШЕНИЕ ЛИМИТА!**\n"
                            f"Ты потратил {total_spent:.2f} ₽ по категории «{category}» в этом месяце, "
                            f"а лимит установлен в {limit_value:.2f} ₽. Лимит превышен на {excess:.2f} ₽!"
                        )

            await db.commit()
            return True, warning

        except Exception as e:
            print(f"Database error: {e}")
            return False, None


async def get_balance(user_id: int) -> float:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0


async def get_last_transactions(user_id: int, limit: int = 10, trans_type: str = "expense"):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT category, amount, date AS created_at
            FROM transactions
            WHERE user_id = ? AND type = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (user_id, trans_type, limit)
        )
        rows = await cursor.fetchall()
        return [
            {
                "category": row["category"],
                "amount": row["amount"],
                "date": row["created_at"]
            }
            for row in rows
        ]


async def get_all_transactions(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT category, amount, type, date AS created_at FROM transactions WHERE user_id = ?",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "category": row["category"],
                "amount": row["amount"],
                "type": row["type"],
                "date": row["created_at"]
            }
            for row in rows
        ]


async def get_expenses_by_period(user_id: int, period: str = "month"):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row

        now = datetime.now()
        if period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "month":
            # Ровно с 1-го числа текущего месяца
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError("Неверный период")

        cursor = await db.execute(
            """
            SELECT category, amount
            FROM transactions
            WHERE user_id = ? AND type = 'expense' AND date >= ?
            ORDER BY date DESC
            """,
            (user_id, start.strftime("%Y-%m-%d %H:%M:%S"))
        )
        rows = await cursor.fetchall()
        return [
            {"category": row["category"], "amount": row["amount"]}
            for row in rows
        ]


async def set_category_limit(user_id: int, category: str, limit: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS category_limits (
                user_id INTEGER,
                category TEXT,
                limit_amount REAL,
                reset_date TEXT,
                PRIMARY KEY (user_id, category)
            )
        """)
        today = datetime.now().strftime("%Y-%m-%d")
        await db.execute(
            "INSERT OR REPLACE INTO category_limits (user_id, category, limit_amount, reset_date) VALUES (?, ?, ?, ?)",
            (user_id, category.lower(), limit, today)
        )
        await db.commit()


async def get_category_limits(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT category, limit_amount FROM category_limits WHERE user_id = ?",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [{"category": row["category"], "limit": row["limit_amount"]} for row in rows]


async def reset_category_limit(user_id: int, category: str):
    async with aiosqlite.connect(DB_NAME) as db:
        today = datetime.now().strftime("%Y-%m-%d")
        await db.execute(
            """
            INSERT OR REPLACE INTO category_limits (user_id, category, limit_amount, reset_date)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, category.lower(), 0.0, today)
        )
        await db.commit()

def compute_next_run(freq: str, from_date):
    if freq == "daily":
        return from_date + timedelta(days=1)
    if freq == "weekly":
        return from_date + timedelta(days=7)
    return from_date + relativedelta(months=1)  # monthly


async def add_recurring_payment(user_id: int, amount: float, category: str,
                                t_type: str, freq: str, action: str):
    from datetime import date as date_cls
    today = date_cls.today()
    next_run = compute_next_run(freq, today).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO recurring_payments (user_id, amount, category, type, freq, action, next_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, amount, category.lower(), t_type, freq, action, next_run)
        )
        await db.commit()


async def get_recurring_payments(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM recurring_payments WHERE user_id = ? ORDER BY next_run",
            (user_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def get_due_recurring_payments(today_str: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM recurring_payments WHERE next_run <= ?",
            (today_str,)
        )
        return [dict(r) for r in await cursor.fetchall()]


async def advance_recurring_payment(payment_id: int, freq: str):
    from datetime import date as date_cls
    d = date_cls.today()
    next_run = compute_next_run(freq, d)
    while next_run.strftime("%Y-%m-%d") <= d.strftime("%Y-%m-%d"):
        next_run = compute_next_run(freq, next_run)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE recurring_payments SET next_run = ? WHERE id = ?",
            (next_run.strftime("%Y-%m-%d"), payment_id)
        )
        await db.commit()


async def delete_recurring_payment(payment_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM recurring_payments WHERE id = ? AND user_id = ?",
            (payment_id, user_id)
        )
        await db.commit()
