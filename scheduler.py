import asyncio
from datetime import datetime

from aiogram import Bot

from database.db_manager import (
    add_transaction,
    get_due_recurring_payments,
    advance_recurring_payment,
)


async def run_recurring_scheduler(bot: Bot):
    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            due = await get_due_recurring_payments(today)

            for p in due:
                if p["action"] == "auto":
                    success, warning = await add_transaction(
                        p["user_id"], p["amount"], p["category"], p["type"]
                    )
                    if success:
                        sign = "-" if p["type"] == "expense" else "+"
                        text = (
                            f"🤖 **Автоплатёж выполнен:** {sign}{p['amount']:.2f} ₽ — {p['category']}"
                        )
                        if warning:
                            text += "\n\n" + warning
                    else:
                        text = "⚠️ Не удалось выполнить автоплатёж."
                else:
                    text = (
                        f"🔔 **Напоминание о платеже:** {p['category']} — {p['amount']:.2f} ₽.\n"
                        "Добавь операцию вручную или настрой автосписание."
                    )

                try:
                    await bot.send_message(p["user_id"], text, parse_mode="Markdown")
                except Exception as e:
                    print(f"Can't send recurring message to {p['user_id']}: {e}")

                await advance_recurring_payment(p["id"], p["freq"])

        except Exception as e:
            print(f"Scheduler error: {e}")

        await asyncio.sleep(60)
