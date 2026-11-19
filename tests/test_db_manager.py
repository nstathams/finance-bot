import asyncio

import pytest

import database.db_manager as dbm

TEST_USER = 999_999_999


async def _cleanup():
    async with dbm.aiosqlite.connect(dbm.DB_NAME) as db:
        await db.execute("DELETE FROM transactions WHERE user_id = ?", (TEST_USER,))
        await db.execute("DELETE FROM category_limits WHERE user_id = ?", (TEST_USER,))
        await db.execute("DELETE FROM users WHERE user_id = ?", (TEST_USER,))
        await db.commit()



@pytest.fixture
def clean_db():
    asyncio.run(dbm.init_db())
    asyncio.run(_cleanup())
    yield
    asyncio.run(_cleanup())


class TestTransactions:
    def test_add_and_balance(self, clean_db):
        async def scenario():
            await dbm.add_transaction(TEST_USER, 1000.0, "тест", "income")
            await dbm.add_transaction(TEST_USER, 300.0, "тест", "expense")
            return await dbm.get_balance(TEST_USER)

        assert asyncio.run(scenario()) == 700.0

    def test_limit_warning_on_exceed(self, clean_db):
        async def scenario():
            await dbm.set_category_limit(TEST_USER, "тест", 100.0)
            w_half = await dbm.add_transaction(TEST_USER, 50.0, "тест", "expense")   # 50% — тишина
            w_over = await dbm.add_transaction(TEST_USER, 60.0, "тест", "expense")   # 110 > 100 — предупреждение
            return w_half, w_over

        w_half, w_over = asyncio.run(scenario())
        assert w_half is None
        assert w_over is not None and "лимит" in w_over.lower()


class TestLimits:
    def test_reset_counter_sets_reset_date(self, clean_db):
        async def scenario():
            await dbm.set_category_limit(TEST_USER, "тест", 100.0)
            limits = await dbm.get_category_limits(TEST_USER)
            await dbm.reset_category_limit(TEST_USER, limits[0]["id"])
            async with dbm.aiosqlite.connect(dbm.DB_NAME) as db:
                cur = await db.execute(
                    "SELECT reset_date FROM category_limits WHERE user_id = ?", (TEST_USER,)
                )
                row = await cur.fetchone()
            return row[0]

        assert asyncio.run(scenario()) is not None

    def test_delete_limit(self, clean_db):
        async def scenario():
            await dbm.set_category_limit(TEST_USER, "тест", 100.0)
            limits = await dbm.get_category_limits(TEST_USER)
            first = await dbm.delete_category_limit(TEST_USER, limits[0]["id"])
            second = await dbm.delete_category_limit(TEST_USER, limits[0]["id"])  # повторно
            return first, second

        first, second = asyncio.run(scenario())
        assert first is True
        assert second is False  # повторное удаление не находит строку
