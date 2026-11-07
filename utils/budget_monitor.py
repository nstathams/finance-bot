async def check_limits(user_id, category, amount, db_conn):
    cursor = await db_conn.execute(
        "SELECT limit_amount, spent FROM budget_limits WHERE user_id=? AND category=?",
        (user_id, category)
    )
    row = await cursor.fetchone()

    if row is None:
        return False, None

    limit_amount, spent = row
    new_spent = spent + amount

    if new_spent > limit_amount:
        return True, new_spent
    return False, new_spent


