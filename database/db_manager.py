import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiosqlite
from dateutil.relativedelta import relativedelta

from utils.errors import DatabaseError

DB_NAME = "finance.db"
logger = logging.getLogger(__name__)

FREQ_STEPS = {
    "daily": lambda d: d + timedelta(days=1),
    "weekly": lambda d: d + timedelta(weeks=1),
    "monthly": lambda d: d + relativedelta(months=1),
}


def get_day_start_end(date: datetime):
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def get_month_start_end(date: datetime):
    start = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start + relativedelta(months=1) - timedelta(microseconds=1)
    return start, end


def _day(value) -> str:
    if isinstance(value, str):
        return value.strip()[:10]
    return value.strftime("%Y-%m-%d")


def _format_date(raw: str) -> str:
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return (raw or "")[:10]


def _period_start(reset_date: Optional[str], now: datetime) -> datetime:
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not reset_date:
        return month_start
    try:
        reset_dt = datetime.strptime(reset_date[:10], "%Y-%m-%d")
    except ValueError:
        return month_start
    return max(month_start, reset_dt)

async def init_db():
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    balance_kop INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount_kop INTEGER,
                    category TEXT,
                    type TEXT,
                    date TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS category_limits (
                    user_id INTEGER,
                    category TEXT,
                    limit_amount REAL,
                    reset_date TEXT,
                    PRIMARY KEY (user_id, category)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS recurring_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount_kop INTEGER,
                    category TEXT,
                    type TEXT,
                    freq TEXT,
                    action TEXT,
                    next_run TEXT
                )
            """)
            await _migrate(db)
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Не удалось инициализировать базу данных")
        raise DatabaseError("Ошибка базы данных при инициализации") from e


async def _migrate(db):
    for table, old_col, new_col in (
        ("transactions", "amount", "amount_kop"),
        ("users", "balance", "balance_kop"),
        ("recurring_payments", "amount", "amount_kop"),
    ):
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = {r[1] for r in await cur.fetchall()}
        if new_col not in cols:
            default = " DEFAULT 0" if table == "users" else ""
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {new_col} INTEGER{default}")
            if old_col in cols:
                await db.execute(
                    f"UPDATE {table} SET {new_col} = CAST(ROUND({old_col} * 100) AS INTEGER)"
                )


# транзакции
async def add_transaction(user_id: int, amount: float, category: str, t_type: str) -> Optional[str]:
    kop = int(round(float(amount) * 100))
    category = (category or "").strip().lower()
    delta = kop if t_type == "income" else -kop
    now = datetime.now()
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """INSERT INTO transactions (user_id, amount_kop, category, type, date)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, kop, category, t_type, now.strftime("%Y-%m-%d %H:%M:%S")),
            )
            await db.execute(
                """INSERT INTO users (user_id, balance_kop) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET balance_kop = balance_kop + ?""",
                (user_id, delta, delta),
            )
            warning = await _check_category_limit(db, user_id, category, now)
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при сохранении транзакции")
        raise DatabaseError("Не удалось сохранить операцию. Попробуй позже.") from e
    return warning


async def get_balance(user_id: int) -> float:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT balance_kop FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
    return (row[0] if row and row[0] is not None else 0) / 100

async def delete_category_limit(user_id: int, limit_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "DELETE FROM category_limits WHERE rowid = ? AND user_id = ?",
            (limit_id, user_id),
        )
        await db.commit()
    return cur.rowcount > 0


async def get_last_transactions(user_id: int, limit: int = 10,
                                trans_type: Optional[str] = None) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT category, amount_kop, date FROM transactions WHERE user_id = ?"
        params: list = [user_id]
        if trans_type:
            query += " AND type = ?"
            params.append(trans_type)
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
    return [
        {
            "category": r["category"],
            "amount": (r["amount_kop"] or 0) / 100,
            "date": _format_date(r["date"]),
        }
        for r in rows
    ]



# лимиты
async def _get_limit_row(db, user_id: int, category: str) -> Optional[Dict[str, Any]]:
    cur = await db.execute(
        """SELECT rowid AS id, category, limit_amount, reset_date
           FROM category_limits WHERE user_id = ? AND category = ?""",
        (user_id, category),
    )
    r = await cur.fetchone()
    return dict(r) if r else None


async def _check_category_limit(db, user_id: int, category: str, now: datetime) -> Optional[str]:
    limit_row = await _get_limit_row(db, user_id, category)
    if not limit_row or not limit_row["limit_amount"]:
        return None
    limit_amount = float(limit_row["limit_amount"])
    period_start = _period_start(limit_row["reset_date"], now)
    cur = await db.execute(
        """SELECT COALESCE(SUM(amount_kop), 0) FROM transactions
           WHERE user_id = ? AND type = 'expense' AND category = ?
             AND substr(date, 1, 10) >= ?""",
        (user_id, category, period_start.strftime("%Y-%m-%d")),
    )
    spent = (await cur.fetchone())[0] / 100
    if spent > limit_amount:
        return (f"⚠️ ПРЕВЫШЕН ЛИМИТ «{category.capitalize()}»: "
                f"{spent:.2f} ₽ из {limit_amount:.2f} ₽")
    if spent > limit_amount * 0.8:
        return (f"🔔 Внимание: траты по «{category.capitalize()}» достигли "
                f"{spent / limit_amount * 100:.0f}% лимита")
    return None


async def get_category_spent_since(user_id: int, category: str) -> float:
    now = datetime.now()
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            limit_row = await _get_limit_row(db, user_id, category.lower())
            period_start = _period_start(limit_row["reset_date"] if limit_row else None, now)
            cur = await db.execute(
                """SELECT COALESCE(SUM(amount_kop), 0) FROM transactions
                   WHERE user_id = ? AND type = 'expense' AND category = ?
                     AND substr(date, 1, 10) >= ?""",
                (user_id, category.lower(), period_start.strftime("%Y-%m-%d")),
            )
            spent = (await cur.fetchone())[0]
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при подсчёте трат по категории")
        raise DatabaseError("Не удалось посчитать траты по категории") from e
    return spent / 100


async def set_category_limit(user_id: int, category: str, limit_amount: float):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                """INSERT INTO category_limits (user_id, category, limit_amount)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, category)
                   DO UPDATE SET limit_amount = excluded.limit_amount""",
                (user_id, category.strip().lower(), float(limit_amount)),
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при установке лимита")
        raise DatabaseError("Не удалось сохранить лимит") from e


async def get_category_limits(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT rowid AS id, category, limit_amount AS limit_, reset_date
               FROM category_limits WHERE user_id = ? ORDER BY category""",
            (user_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "category": r["category"],
            "limit": r["limit_"],
            "reset_date": r["reset_date"],
        }
        for r in rows
    ]


async def get_limit_by_id(limit_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT rowid AS id, category, limit_amount AS limit_, reset_date
               FROM category_limits WHERE rowid = ? AND user_id = ?""",
            (limit_id, user_id),
        )
        r = await cur.fetchone()
    if not r:
        return None
    return {"id": r["id"], "category": r["category"], "limit": r["limit_"],
            "reset_date": r["reset_date"]}


async def reset_category_limit(user_id: int, limit_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE category_limits SET reset_date = ? WHERE rowid = ? AND user_id = ?",
                (datetime.now().strftime("%Y-%m-%d"), limit_id, user_id),
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при сбросе лимита")
        raise DatabaseError("Не удалось сбросить счётчик") from e


# периоды
_PERIOD_RANGES = {
    "day": lambda now: now.replace(hour=0, minute=0, second=0, microsecond=0),
    "week": lambda now: (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0),
    "month": lambda now: now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    "year": lambda now: now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
}


async def get_expenses_by_period(user_id: int, period: str = "month") -> List[Dict[str, Any]]:
    if period not in _PERIOD_RANGES:
        raise ValueError(f"Неизвестный период: {period}")
    start = _PERIOD_RANGES[period](datetime.now()).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT category, SUM(amount_kop) AS total
                   FROM transactions
                   WHERE user_id = ? AND type = 'expense'
                     AND substr(date, 1, 10) BETWEEN ? AND ?
                   GROUP BY category ORDER BY total DESC""",
                (user_id, start, end),
            )
            rows = await cur.fetchall()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при выборке расходов за период")
        raise DatabaseError("Не удалось получить расходы за период") from e
    return [{"category": r["category"], "amount": (r["total"] or 0) / 100} for r in rows]


async def get_period_stats(user_id: int, start_date, end_date) -> Dict[str, Any]:
    start_str = _day(start_date)
    end_str = _day(end_date)
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT
                       COALESCE(SUM(CASE WHEN type = 'expense' THEN amount_kop ELSE 0 END), 0) AS total_expense,
                       COALESCE(SUM(CASE WHEN type = 'income' THEN amount_kop ELSE 0 END), 0) AS total_income
                   FROM transactions
                   WHERE user_id = ? AND substr(date, 1, 10) BETWEEN ? AND ?""",
                (user_id, start_str, end_str),
            )
            row = await cur.fetchone()
            cur = await db.execute(
                """SELECT category, SUM(amount_kop) AS total
                   FROM transactions
                   WHERE user_id = ? AND type = 'expense'
                     AND substr(date, 1, 10) BETWEEN ? AND ?
                   GROUP BY category ORDER BY total DESC""",
                (user_id, start_str, end_str),
            )
            cat_rows = await cur.fetchall()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при формировании отчёта")
        raise DatabaseError("Не удалось сформировать отчёт") from e
    return {
        "total_expense": (row["total_expense"] or 0) / 100,
        "total_income": (row["total_income"] or 0) / 100,
        "categories": [
            {"category": r["category"], "amount": (r["total"] or 0) / 100}
            for r in cat_rows
        ],
    }


# CSV
async def export_transactions_to_csv_rows(user_id: int) -> List[List[str]]:
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute(
                """SELECT date, category, type, amount_kop FROM transactions
                   WHERE user_id = ? ORDER BY date DESC""",
                (user_id,),
            )
            rows = await cur.fetchall()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при экспорте CSV")
        raise DatabaseError("Не удалось выгрузить историю операций") from e

    csv_data = [["Дата", "Категория", "Тип операции", "Сумма (₽)"]]
    for raw_date, category, t_type, kop in rows:
        csv_data.append([
            _format_date(raw_date),
            (category or "").capitalize(),
            "Пополнение" if t_type == "income" else "Трата",
            f"{(kop or 0) / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽",
        ])
    return csv_data


# регулярные платежи
async def add_recurring_payment(user_id: int, amount: float, category: str,
                                t_type: str, freq: str, action: str):
    kop = int(round(float(amount) * 100))
    step = FREQ_STEPS.get(freq, FREQ_STEPS["monthly"])
    now = datetime.now()
    next_run = step(now).strftime("%Y-%m-%d")
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                """INSERT INTO recurring_payments
                   (user_id, amount_kop, category, type, freq, action, next_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, kop, category.lower(), t_type, freq, action, next_run),
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при добавлении регулярного платежа")
        raise DatabaseError("Не удалось сохранить платёж") from e


async def get_recurring_payments(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, user_id, amount_kop, category, type, freq, action, next_run
               FROM recurring_payments WHERE user_id = ? ORDER BY next_run""",
            (user_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"], "user_id": r["user_id"],
            "amount": (r["amount_kop"] or 0) / 100, "amount_kop": r["amount_kop"],
            "category": r["category"], "type": r["type"],
            "freq": r["freq"], "action": r["action"], "next_run": r["next_run"],
        }
        for r in rows
    ]


async def get_due_recurring_payments(today: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, user_id, amount_kop, category, type, freq, action, next_run
               FROM recurring_payments WHERE substr(next_run, 1, 10) <= ?
               ORDER BY next_run""",
            (today,),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"], "user_id": r["user_id"],
            "amount": (r["amount_kop"] or 0) / 100, "amount_kop": r["amount_kop"],
            "category": r["category"], "type": r["type"],
            "freq": r["freq"], "action": r["action"], "next_run": r["next_run"],
        }
        for r in rows
    ]


async def process_recurring_payment(payment: Dict[str, Any]) -> Optional[str]:
    now = datetime.now()
    freq = payment["freq"]
    step = FREQ_STEPS.get(freq)
    if step is None:
        logger.error("Неизвестная периодичность '%s' у платежа id=%s", freq, payment["id"])
        step = FREQ_STEPS["monthly"]
        freq = "monthly"
    planned = datetime.strptime(payment["next_run"][:10], "%Y-%m-%d")
    guard = 0
    while planned <= now and guard < 400:
        planned = step(planned)
        guard += 1

    kop = int(payment["amount_kop"] or 0)
    delta = kop if payment["type"] == "income" else -kop
    action = payment.get("action", "auto")
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            warning: Optional[str] = None
            if action == "auto":
                await db.execute(
                    """INSERT INTO transactions (user_id, amount_kop, category, type, date)
                       VALUES (?, ?, ?, ?, ?)""",
                    (payment["user_id"], kop, payment["category"], payment["type"],
                     now.strftime("%Y-%m-%d %H:%M:%S")),
                )
                await db.execute(
                    """INSERT INTO users (user_id, balance_kop) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET balance_kop = balance_kop + ?""",
                    (payment["user_id"], delta, delta),
                )
                warning = await _check_category_limit(db, payment["user_id"],
                                                      payment["category"], now)
            await db.execute(
                "UPDATE recurring_payments SET next_run = ?, freq = ? WHERE id = ?",
                (planned.strftime("%Y-%m-%d"), freq, payment["id"]),
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при обработке регулярного платежа id=%s", payment["id"])
        raise DatabaseError("Не удалось обработать регулярный платёж") from e
    return warning



async def delete_recurring_payment(payment_id: int, user_id: int):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "DELETE FROM recurring_payments WHERE id = ? AND user_id = ?",
                (payment_id, user_id),
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.exception("Ошибка БД при удалении регулярного платежа")
        raise DatabaseError("Не удалось удалить платёж") from e
