import asyncio
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv
from database.db_manager import init_db
from handlers.transaction_handler import router as transaction_router
from scheduler import run_recurring_scheduler

load_dotenv()


async def main():
    # 1. Инициализация БД
    await init_db()

    # 2. Настройка бота
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    dp = Dispatcher()

    # 3. Регистрация роутеров (модулей)
    dp.include_router(transaction_router)
    asyncio.create_task(run_recurring_scheduler(bot))

    # 4. Запуск
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
