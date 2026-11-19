import asyncio
import logging
from datetime import datetime
from html import escape
from utils.errors import DatabaseError
from aiogram import Bot

from database.db_manager import (
    get_due_recurring_payments,
    process_recurring_payment,
)

log = logging.getLogger(__name__)


async def run_recurring_scheduler(bot: Bot):
    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            due = await get_due_recurring_payments(today)

            for p in due:
                try:
                    warning = await process_recurring_payment(p)
                except DatabaseError:
                    log.exception("Не удалось обработать платёж id=%s", p["id"])
                    continue

                if p["action"] == "auto":
                    sign = "-" if p["type"] == "expense" else "+"
                    text = (
                        f"🤖 <b>Автоплатёж выполнен:</b> {sign}{p['amount']:.2f} ₽ — "
                        f"{escape(p['category'])}"
                    )
                    if warning:
                        text += "\n\n" + warning
                else:
                    text = (
                        f"🔔 <b>Напоминание о платеже:</b> {escape(p['category'])} — "
                        f"{p['amount']:.2f} ₽.\nДобавь операцию вручную или настрой автосписание."
                    )

                try:
                    await bot.send_message(p["user_id"], text)
                except Exception:
                    log.exception("Не удалось отправить сообщение пользователю %s", p["user_id"])

        except Exception:
            log.exception("Ошибка планировщика")

        await asyncio.sleep(60)
