from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PERIOD_NAMES_RU = {
    "day": "день",
    "week": "неделю",
    "month": "месяц",
    "year": "год",
    "today": "сегодня",
    "week_label": "эту неделю",
    "month_label": "этот месяц",
    "year_label": "этот год",
}

REPORT_TITLES_RU = {
    "report_today": "Сегодня",
    "report_week": "Неделя",
    "report_month": "Месяц",
    "report_year": "Год",
    "report_custom": "Произвольный период",
}


def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📜 История трат", callback_data="history_expenses"),
            InlineKeyboardButton(text="💰 Пополнения", callback_data="history_income"),
        ],
        [InlineKeyboardButton(text="➕ Добавить операцию", callback_data="start_add_transaction")],
        [InlineKeyboardButton(text="🔁 Регулярные платежи", callback_data="recurring_menu")],
        [
            InlineKeyboardButton(text="📊 Визуализация", callback_data="show_chart"),
            InlineKeyboardButton(text="🎯 Лимиты", callback_data="open_limits_menu"),
        ],
        [InlineKeyboardButton(text="📊 Отчёт и аналитика", callback_data="open_report_menu")],
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export_csv")],
    ])


def get_report_period_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 За сегодня", callback_data="report_today"),
            InlineKeyboardButton(text="🗓 За неделю", callback_data="report_week"),
        ],
        [
            InlineKeyboardButton(text="📆 За месяц", callback_data="report_month"),
            InlineKeyboardButton(text="📜 За год", callback_data="report_year"),
        ],
        [InlineKeyboardButton(text="🎯 Произвольный период", callback_data="report_custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")],
    ])


def get_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
    )


def get_period_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 За день", callback_data="period_day"),
            InlineKeyboardButton(text="🗓 За неделю", callback_data="period_week"),
        ],
        [
            InlineKeyboardButton(text="📆 За месяц", callback_data="period_month"),
            InlineKeyboardButton(text="📜 За год", callback_data="period_year"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_chart")],
    ])


def make_main_menu_text(username: str, balance: float) -> str:
    return (
        f"👋 Привет, {escape(username)}!\n\n"
        f"💰 Твой баланс: <b>{balance:.2f} ₽</b>\n\n"
        "Главное меню — выбери действие:"
    )
