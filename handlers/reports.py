import csv
import logging
from datetime import datetime, timedelta
from html import escape
from io import StringIO

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from aiogram.types import BufferedInputFile
from utils.errors import DatabaseError
from utils.chart_generator import generate_pie_chart
from database.db_manager import (
    get_balance,
    get_last_transactions,
    get_expenses_by_period,
    get_period_stats,
    get_day_start_end,
    get_month_start_end,
    export_transactions_to_csv_rows,
)
from .keyboards import (
    get_main_menu_keyboard,
    get_report_period_keyboard,
    get_period_keyboard,
    PERIOD_NAMES_RU,
    REPORT_TITLES_RU,
    make_main_menu_text,
)

router = Router()


class ReportPeriodState(StatesGroup):
    waiting_for_start_date = State()
    waiting_for_end_date = State()


def _fmt(value: float) -> str:
    """Число с пробелами-разделителями тысяч: 12 345,60"""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


# Отчёты за фиксированные периоды

@router.callback_query(F.data == "open_report_menu")
async def open_report_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "📈 <b>Финансовый отчёт</b>\n\n"
        "Выбери период, за который хочешь увидеть статистику:",
        reply_markup=get_report_period_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"report_today", "report_week", "report_month", "report_year"}))
async def handle_report(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.now()

    if callback.data == "report_today":
        start, end = get_day_start_end(now)
    elif callback.data == "report_week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif callback.data == "report_month":
        start, end = get_month_start_end(now)
    else:  # report_year
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now

    stats = await get_period_stats(user_id, start, end)

    title = REPORT_TITLES_RU[callback.data]
    text = f"📊 <b>Отчёт: {title}</b>\n\n"
    text += f"🗓 Период: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}\n"
    text += f"💰 Траты: <b>{_fmt(stats['total_expense'])} ₽</b>\n"
    text += f"💵 Пополнения: <b>{_fmt(stats['total_income'])} ₽</b>\n"
    text += f"📉 Сальдо: <b>{_fmt(stats['total_income'] - stats['total_expense'])} ₽</b>\n\n"

    if stats["categories"]:
        text += "📁 <b>Топ категорий:</b>\n"
        for item in stats["categories"][:5]:
            percent = (item["amount"] / stats["total_expense"] * 100) if stats["total_expense"] > 0 else 0
            text += f"- {escape(item['category'].capitalize())}: {_fmt(item['amount'])} ₽ ({percent:.1f}%)\n"
    else:
        text += "🤫 За этот период нет трат."

    await callback.message.edit_text(text, reply_markup=get_report_period_keyboard())
    await callback.answer()


# Произвольный период через календарь

@router.callback_query(F.data == "report_custom")
async def report_custom_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportPeriodState.waiting_for_start_date)
    calendar = SimpleCalendar()
    await callback.message.edit_text(
        "📅 <b>Выберите дату начала периода:</b>\n\n"
        "Используйте стрелки для переключения месяца/года.",
        reply_markup=await calendar.start_calendar(),
    )
    await callback.answer()


@router.callback_query(SimpleCalendarCallback.filter(), ReportPeriodState.waiting_for_start_date)
async def process_start_date(callback: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    is_selected, selected_date = await SimpleCalendar().process_selection(callback, callback_data)
    if not is_selected:
        await callback.answer()
        return

    await state.update_data(start_date=selected_date)
    await state.set_state(ReportPeriodState.waiting_for_end_date)

    calendar = SimpleCalendar()
    await callback.message.edit_text(
        f"✅ Дата начала: {selected_date.strftime('%d.%m.%Y')}\n\n"
        "📅 Теперь выбери дату окончания периода:",
        reply_markup=await calendar.start_calendar(),
    )
    await callback.answer()


@router.callback_query(SimpleCalendarCallback.filter(), ReportPeriodState.waiting_for_end_date)
async def process_end_date(callback: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    is_selected, end_date = await SimpleCalendar().process_selection(callback, callback_data)
    if not is_selected:
        await callback.answer()
        return

    data = await state.get_data()
    start_date = data.get("start_date")
    if start_date is None:
        await callback.answer("Дата начала не сохранена. Начни заново.", show_alert=True)
        await state.clear()
        return

    if start_date > end_date:
        await callback.answer("Дата окончания не может быть раньше даты начала!", show_alert=True)
        return

    stats = await get_period_stats(callback.from_user.id, start_date, end_date)

    text = (
        f"📊 <b>Отчёт за период:</b> {start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}\n\n"
        f"💰 Пополнения: {_fmt(stats['total_income'])} ₽\n"
        f"💸 Траты: {_fmt(stats['total_expense'])} ₽\n"
        f"📉 Сальдо: {_fmt(stats['total_income'] - stats['total_expense'])} ₽\n\n"
        "<b>Детализация по категориям:</b>\n"
    )
    categories = stats.get("categories", [])
    if categories:
        for item in categories:
            text += f"   • {escape(item['category'].capitalize())}: {_fmt(item['amount'])} ₽\n"
    else:
        text += "   • Нет трат за этот период."

    await callback.message.edit_text(text, reply_markup=get_report_period_keyboard())
    await state.clear()
    await callback.answer()


# Диаграммы

@router.callback_query(F.data == "show_chart")
async def show_chart_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите период для круговой диаграммы расходов:",
        reply_markup=get_period_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("period_"))
async def send_period_chart(callback: CallbackQuery):
    user_id = callback.from_user.id
    period_map = {
        "period_day": "day",
        "period_week": "week",
        "period_month": "month",
        "period_year": "year",
    }
    period = period_map.get(callback.data)
    if not period:
        await callback.answer("Ошибка выбора периода", show_alert=True)
        return

    transactions = await get_expenses_by_period(user_id, period=period)

    if not transactions:
        text = f"📊 Нет расходов за {PERIOD_NAMES_RU[period]}."
        balance = await get_balance(user_id)
        username = callback.from_user.first_name or "Пользователь"
        await callback.message.edit_text(text)
        await callback.message.answer(
            make_main_menu_text(username, balance),
            reply_markup=get_main_menu_keyboard(),
        )
        await callback.answer()
        return

    photo = generate_pie_chart(transactions, title=f"Расходы за {PERIOD_NAMES_RU[period]}")
    total_spent = sum(t["amount"] for t in transactions)
    caption = f"📊 Расходы за {PERIOD_NAMES_RU[period]}\nВсего потрачено: {_fmt(total_spent)} ₽"

    await callback.message.answer_photo(photo=photo, caption=caption)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_chart")
async def cancel_chart(callback: CallbackQuery):
    balance = await get_balance(callback.from_user.id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.edit_text(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


# История операций

@router.callback_query(F.data == "history_expenses")
async def show_expenses(callback: CallbackQuery):
    user_id = callback.from_user.id
    history = await get_last_transactions(user_id, limit=10, trans_type="expense")

    if not history:
        text = "📜 Истории трат пока нет."
    else:
        lines = [f"• {escape(t['category'].capitalize())}: {t['amount']} ₽ ({t['date']})" for t in history]
        text = "📜 Последние траты:\n" + "\n".join(lines)

    await callback.message.answer(text)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "history_income")
async def show_income(callback: CallbackQuery):
    user_id = callback.from_user.id
    history = await get_last_transactions(user_id, limit=10, trans_type="income")

    if not history:
        text = "💰 Истории пополнений пока нет."
    else:
        lines = [f"- {escape(t['category'])}: {t['amount']} ₽ ({t['date']})" for t in history]
        text = "💰 Последние пополнения:\n" + "\n".join(lines)

    await callback.message.answer(text)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
    )
    await callback.answer()


# Экспорт CSV

@router.callback_query(F.data == "export_csv")
async def export_csv_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.first_name or "Пользователь"

    await callback.answer("⏳ Формирую файл с историей операций...")

    try:
        csv_rows = await export_transactions_to_csv_rows(user_id)

        if len(csv_rows) <= 1:
            await callback.message.answer(
                "📭 У тебя пока нет транзакций для экспорта.",
                reply_markup=get_main_menu_keyboard(),
            )
            return

        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerows(csv_rows)
        csv_content = output.getvalue()
        output.close()

        filename = f"transactions_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        file_to_send = BufferedInputFile(csv_content.encode("utf-8-sig"), filename=filename)

        await callback.message.answer_document(
            document=file_to_send,
            caption=(
                f"💾 <b>Файл истории операций для {escape(username)}</b>\n\n"
                "В файле:\n"
                "- Дата (день.месяц.год)\n"
                "- Категория\n"
                "- Тип операции (Трата/Пополнение)\n"
                "- Сумма в рублях."
            ),
        )

    except DatabaseError:
        logging.exception("Ошибка экспорта CSV")
        await callback.message.answer("⚠️ Произошла ошибка при формировании файла. Попробуй позже.")

