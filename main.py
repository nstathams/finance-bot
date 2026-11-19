import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ErrorEvent
from dotenv import load_dotenv

from database.db_manager import init_db
from handlers.transactions import router as transactions_router
from handlers.limits import router as limits_router
from handlers.recurring import router as recurring_router
from handlers.reports import router as reports_router
from scheduler import run_recurring_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def global_error_handler(event: ErrorEvent):
    logging.exception("Необработанная ошибка", exc_info=event.exception)
    message = event.update.message if event.update else None
    if message:
        try:
            await message.answer("⚠️ Произошла ошибка. Попробуй ещё раз позже.")
        except Exception:
            logging.exception("Не удалось отправить сообщение об ошибке пользователю")


async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Токен не найден. Создай файл .env со строкой: TELEGRAM_BOT_TOKEN=твой_токен"
        )

    await init_db()

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.errors.register(global_error_handler)

    dp.include_router(transactions_router)
    dp.include_router(limits_router)
    dp.include_router(recurring_router)
    dp.include_router(reports_router)

    asyncio.create_task(run_recurring_scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

