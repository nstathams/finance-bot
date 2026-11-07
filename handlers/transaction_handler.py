
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from aiogram import Router, F
from io import StringIO
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile
from aiogram.filters import Command
import csv
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from utils.chart_generator import generate_pie_chart
from database.db_manager import (
    add_transaction,
    get_balance,
    get_last_transactions,
    get_expenses_by_period,
    set_category_limit,
    get_category_limits,
    reset_category_limit,
    export_transactions_to_csv_rows,
    get_day_start_end,
    get_month_start_end,
    get_period_stats,
    add_recurring_payment,  # ← новые
    get_recurring_payments,
    get_due_recurring_payments,
    advance_recurring_payment,
    delete_recurring_payment,
)
from parsers.transaction_parser import parse_transaction

router = Router()

user_states = {}
ADD_TRANSACTION_MODE = "add_transaction"
SET_LIMIT_MODE = "set_limit_mode"
SET_NEW_CATEGORY_MODE = "set_new_category_mode"


class ReportPeriodState(StatesGroup):
    waiting_for_start_date = State()
    waiting_for_end_date = State()


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
                reply_markup=get_main_menu_keyboard()
            )
            return

        # Создаём CSV в памяти
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerows(csv_rows)
        csv_content = output.getvalue()
        output.close()

        # Готовим файл для отправки
        filename = f"transactions_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        file_to_send = BufferedInputFile(
            csv_content.encode("utf-8-sig"),
            filename=filename
        )

        await callback.message.answer_document(
            document=file_to_send,
            caption=(
                f"💾 **Файл истории операций для {username}**\n\n"
                "В файле:\n"
                "- Дата (день.месяц.год)\n"
                "- Категория\n"
                "- Тип операции (Трата/Пополнение)\n"
                "- Сумма в рублях."
            ),
            parse_mode="Markdown"
        )

    except Exception as e:
        print(f"Error exporting CSV: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при формировании файла. Попробуй позже.")
        
def get_main_menu_keyboard():
    buttons = [
        [
            InlineKeyboardButton(text="📜 История трат", callback_data="history_expenses"),
            InlineKeyboardButton(text="💰 Пополнения", callback_data="history_income"),
        ],

        [InlineKeyboardButton(text="➕ Добавить операцию", callback_data="start_add_transaction")],

        [InlineKeyboardButton(text="🔁 Регулярные платежи", callback_data="recurring_menu")],

        [
            InlineKeyboardButton(text="📊 Визуализация", callback_data="show_chart"),
            InlineKeyboardButton(text="🎯 Лимиты", callback_data="open_limits_menu")
        ],

        [InlineKeyboardButton(text="📊 Отчёт и аналитика", callback_data="open_report_menu")],

        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data="export_csv")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_report_period_keyboard():
    buttons = [
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
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]]
    )


def get_period_keyboard():
    buttons = [
        [
            InlineKeyboardButton(text="📅 За день", callback_data="period_day"),
            InlineKeyboardButton(text="🗓 За неделю", callback_data="period_week"),
        ],
        [
            InlineKeyboardButton(text="📆 За месяц", callback_data="period_month"),
            InlineKeyboardButton(text="📜 За год", callback_data="period_year"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_chart")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def make_main_menu_text(username: str, balance: float) -> str:
    return (
        f"👋 Привет, {username}!\n\n"
        f"💰 Твой баланс: **{balance:.2f} ₽**\n\n"
        "Главное меню — выбери действие:"
    )


@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.first_name or "Пользователь"
    balance = await get_balance(user_id)

    await message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )


#Отмена (универсальная)
@router.callback_query(F.data == "cancel_action")
async def cancel_action_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    user_id = callback.from_user.id
    # Сброс любых активных режимов
    if user_states.get(user_id):
        user_states.pop(user_id, None)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"

    await callback.message.edit_text(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()



# Добавление операции
@router.callback_query(F.data == "start_add_transaction")
async def start_add_transaction_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_states[user_id] = ADD_TRANSACTION_MODE

    text = (
        "📝 **Как добавить операцию:**\n\n"
        "- Сначала знак (+ или -), потом число, потом категория.\n"
        "✅ Примеры: `-300 кофе`, `+5000 зарплата`.\n\n"
        "👇 Напиши сообщение в таком формате:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.message(F.text)
async def handle_all_messages(message: Message):
    user_id = message.from_user.id
    text = message.text
    state = user_states.get(user_id)

    # Режим: ввод суммы и категории для регулярного платежа
    if isinstance(state, tuple) and state[0] == ADD_RECURRING_MODE:
        freq = state[1]
        data, error = parse_transaction(text)
        if error:
            await message.answer(f"⚠️ {error}", reply_markup=get_cancel_keyboard())
            return
        user_states[user_id] = ("recurring_action", freq, data)
        await message.answer(
            f"✅ Понял: {data['amount']:.2f} ₽, «{data['category']}».\n\n"
            "Что делать в дату платежа?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Списать автоматически", callback_data="recur_action_auto")],
                [InlineKeyboardButton(text="🔔 Только напомнить", callback_data="recur_action_remind")],
            ])
        )
        return

    # 1. Режим: Ввод названия новой категории для лимита
    if isinstance(state, tuple) and state[0] == SET_NEW_CATEGORY_MODE:
        category = text.strip().lower()
        if not category:
            await message.answer("Название категории не может быть пустым. Введи название:")
            return
        # Переключаем на ввод суммы, сохраняя категорию
        user_states[user_id] = (SET_LIMIT_MODE, category)
        await message.answer(f"Категория установлена: **«{category}»**. Теперь введи лимит (число, например 5000):")
        return

    # 2. Режим: Установка лимита (ввод суммы)
    if isinstance(state, tuple) and state[0] == SET_LIMIT_MODE:
        mode, category = state
        try:
            limit_val = float(text.replace(",", "."))
            if limit_val < 0:
                await message.answer("Лимит не может быть отрицательным. Введи положительное число:")
                return

            await set_category_limit(user_id, category, limit_val)

            await message.answer(
                f"✅ Лимит для **«{category.capitalize()}»** установлен: **{limit_val:.2f} ₽**.\nВозвращаю в главное меню.",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )
            user_states.pop(user_id, None)
            return
        except ValueError:
            await message.answer("⚠️ Введи только число (например, 5000 или 1200.50). Не пиши слова:")
            return

    # 3. Режим: Добавление операции (ввод транзакции)
    if state == ADD_TRANSACTION_MODE:
        data, error = parse_transaction(text)
        if error:
            await message.answer(
                f"⚠️ Ошибка формата: {error}\n\nПопробуй ещё раз в формате: `-300 кофе`",
                reply_markup=get_cancel_keyboard()
            )
            return

        success, warning = await add_transaction(
            user_id,
            data["amount"],
            data["category"],
            data["type"]
        )

        if not success:
            await message.answer("Ошибка сохранения. Попробуй позже.")
            return

        balance = await get_balance(user_id)
        status_emoji = "📉" if data["type"] == "expense" else "📈"

        response_text = (
            f"{status_emoji} Операция сохранена!\n"
            f"Категория: {data['category'].capitalize()}\n"
            f"Сумма: {data['amount']} ₽\n"
            f"Твой баланс: {balance:.2f} ₽"
        )
        if warning:
            response_text += "\n\n" + warning

        await message.answer(
            response_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
        user_states.pop(user_id, None)
        return

    # 4. Обычный ввод (быстрая транзакция вне режима)
    data, error = parse_transaction(text)
    if data and not error:
        try:
            success, warning = await add_transaction(
                user_id,
                data["amount"],
                data["category"],
                data["type"]
            )

            if not success:
                return

            balance = await get_balance(user_id)
            status_emoji = "📉" if data["type"] == "expense" else "📈"

            response_text = (
                f"{status_emoji} Транзакция сохранена!\n"
                f"Текущий баланс: {balance:.2f} ₽"
            )

            if warning:
                response_text += "\n\n" + warning

            await message.answer(
                response_text,
                reply_markup=get_main_menu_keyboard(),
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            print(f"Error in quick transaction: {e}")


ADD_RECURRING_MODE = "recurring_amount"

FREQ_LABELS = {"daily": "каждый день", "weekly": "каждую неделю", "monthly": "каждый месяц"}


def get_recurring_menu_keyboard(payments):
    buttons = [[InlineKeyboardButton(text="➕ Добавить платёж", callback_data="recurring_add")]]
    for p in payments:
        freq = FREQ_LABELS.get(p["freq"], p["freq"])
        mode = "🤖 авто" if p["action"] == "auto" else "🔔 напом."
        sign = "+" if p["type"] == "income" else "-"
        buttons.append([
            InlineKeyboardButton(
                text=f"{'-' if p['type']=='expense' else '+'}{p['amount']:.0f} {p['category']} ({freq}, с {p['next_run']}) {mode}",
                callback_data=f"recurring_del_{p['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "recurring_menu")
async def recurring_menu_handler(callback: CallbackQuery):
    payments = await get_recurring_payments(callback.from_user.id)
    text = (
        "🔁 **Регулярные платежи**\n\n"
        "Здесь настраиваются постоянные расходы и доходы: подписки, аренда, зарплата.\n"
        "- 🤖 **Автосписание** — бот сам добавляет операцию в нужную дату.\n"
        "- 🔔 **Напоминание** — бот присылает уведомление, операцию добавляешь ты.\n\n"
        "Нажми на платёж, чтобы удалить его:"
    )
    await callback.message.edit_text(text, reply_markup=get_recurring_menu_keyboard(payments),
                                     parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "recurring_add")
async def recurring_add_start(callback: CallbackQuery):
    buttons = [
        [InlineKeyboardButton(text="📅 Каждый день", callback_data="recur_freq_daily")],
        [InlineKeyboardButton(text="🗓 Каждую неделю", callback_data="recur_freq_weekly")],
        [InlineKeyboardButton(text="📆 Каждый месяц", callback_data="recur_freq_monthly")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="recurring_menu")],
    ]
    await callback.message.edit_text(
        "🔁 **Новый регулярный платёж**\n\nВыбери периодичность:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recur_freq_"))
async def recurring_freq_chosen(callback: CallbackQuery, state: FSMContext):
    freq = callback.data.replace("recur_freq_", "")
    user_states[callback.from_user.id] = (ADD_RECURRING_MODE, freq)

    await callback.message.edit_text(
        f"🔁 Периодичность: **{FREQ_LABELS[freq]}**.\n\n"
        "Теперь напиши операцию в привычном формате, например: `-500 подписка`\n"
        "Знак определяет тип: траты (−) или пополнение (+).",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recur_action_"))
async def recurring_action_chosen(callback: CallbackQuery):
    user_id = callback.from_user.id
    state = user_states.get(user_id)
    if not (isinstance(state, tuple) and state[0] == "recurring_action"):
        await callback.answer("Сессия истекла, начни заново", show_alert=True)
        return

    _, freq, data = state
    action = "auto" if callback.data == "recur_action_auto" else "remind"

    await add_recurring_payment(user_id, data["amount"], data["category"], data["type"], freq, action)
    user_states.pop(user_id, None)

    await callback.message.edit_text(
        f"✅ Платёж «{data['category']}» на {data['amount']:.2f} ₽ добавлен.\n"
        f"Периодичность: {FREQ_LABELS[freq]}. Режим: {'🤖 автосписание' if action == 'auto' else '🔔 напоминание'}.",
        reply_markup=get_recurring_menu_keyboard(await get_recurring_payments(user_id)),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recurring_del_"))
async def recurring_delete_handler(callback: CallbackQuery):
    payment_id = int(callback.data.replace("recurring_del_", ""))
    await delete_recurring_payment(payment_id, callback.from_user.id)
    payments = await get_recurring_payments(callback.from_user.id)
    await callback.message.edit_text(
        "🗑 Платёж удалён. Обновлённый список:",
        reply_markup=get_recurring_menu_keyboard(payments)
    )
    await callback.answer()


@router.callback_query(F.data == "open_limits_menu")
async def open_limits_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    limits = await get_category_limits(user_id)

    instruction_text = (
        "🎯 **Меню лимитов категорий**\n\n"
        "Здесь ты можешь контролировать траты по категориям.\n\n"
        "💡 **Как это работает:**\n"
        "- Если ты установишь лимит 500 ₽ на «кофе», бот будет предупреждать, если траты превысят эту сумму.\n"
        "- Лимит можно сбросить в любой момент.\n\n"
        "👇 Ниже список текущих лимитов и кнопки для управления:"
    )

    buttons_rows = []

    if not limits:
        buttons_rows.append([InlineKeyboardButton(text="⚠️ Лимитов пока нет", callback_data="none")])
    else:
        for item in limits:
            cat = item['category']
            lim = item['limit']
            display_lim = f"{lim:.2f} ₽" if lim is not None else "Не установлен"

            row = [
                InlineKeyboardButton(
                    text=f"☕️ {cat.capitalize()}: {display_lim}",
                    callback_data=f"limit_view_{cat}"
                ),
                InlineKeyboardButton(
                    text="🔧 Изменить",
                    callback_data=f"limit_set_{cat}"
                )
            ]
            buttons_rows.append(row)

    buttons_rows.extend([
        [InlineKeyboardButton(text="➕ Установить лимит для новой категории", callback_data="limit_create_new")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons_rows)

    await callback.message.edit_text(instruction_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("limit_set_"))
async def start_set_limit(callback: CallbackQuery):
    user_id = callback.from_user.id
    category = callback.data.replace("limit_set_", "")

    user_states[user_id] = (SET_LIMIT_MODE, category)

    text = (
        f"💵 Установка лимита для категории **«{category.capitalize()}»**.\n\n"
        "Введи желаемую сумму лимита (только число):\n"
        "Например: `3000` или `150.75`"
    )
    await callback.message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "limit_create_new")
async def create_new_limit_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_states[user_id] = (SET_NEW_CATEGORY_MODE, None)

    text = (
        "📝 Создание лимита для новой категории.\n\n"
        "Введи название категории (например, «такси», «одежда»):"
    )
    await callback.message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "history_expenses")
async def show_expenses(callback: CallbackQuery):
    user_id = callback.from_user.id
    history = await get_last_transactions(user_id, limit=10, trans_type="expense")

    if not history:
        text = "📜 Истории трат пока нет."
    else:
        lines = [f"• {t['category'].capitalize()}: {t['amount']} ₽ ({t['date']})" for t in history]
        text = "📜 Последние траты:\n" + "\n".join(lines)

    await callback.message.answer(text)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "history_income")
async def show_income(callback: CallbackQuery):
    user_id = callback.from_user.id
    history = await get_last_transactions(user_id, limit=10, trans_type="income")

    if not history:
        text = "💰 Истории пополнений пока нет."
    else:
        lines = [f"- {t['category']}: {t['amount']} ₽ ({t['date']})" for t in history]
        text = "💰 Последние пополнения:\n" + "\n".join(lines)

    await callback.message.answer(text)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "show_chart")
async def show_chart_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите период для круговой диаграммы расходов:",
        reply_markup=get_period_keyboard()
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
        text = f"📊 Нет расходов за выбранный период ({period})."
        balance = await get_balance(user_id)
        username = callback.from_user.first_name or "Пользователь"
        await callback.message.edit_text(text)
        await callback.message.answer(
            make_main_menu_text(username, balance),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    photo = generate_pie_chart(transactions, title=f"Расходы за {period}")
    total_spent = sum(t['amount'] for t in transactions)
    caption = (
        f"📊 Расходы за период: {period}\n"
        f"Всего потрачено: {total_spent:,.2f} ₽"
    )

    await callback.message.answer_photo(photo=photo, caption=caption)

    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    await callback.message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_chart")
async def cancel_chart(callback: CallbackQuery):
        """Отмена выбора периода диаграммы — возврат в главное меню"""
        balance = await get_balance(callback.from_user.id)
        username = callback.from_user.first_name or "Пользователь"

        await callback.message.edit_text(
            make_main_menu_text(username, balance),
            reply_markup=get_main_menu_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()

@router.callback_query(F.data.startswith("limit_view_"))
async def view_limit_details(callback: CallbackQuery):
        category = callback.data.replace("limit_view_", "")
        user_id = callback.from_user.id

        # Получаем текущий лимит
        limit_data = await get_category_limits(user_id)
        limit = None
        for item in limit_data:
            if item['category'] == category:
                limit = item['limit']
                break

        text = (
            f"🎯 **Лимит категории: {category.capitalize()}**\n\n"
            f"Текущий лимит: {limit:.2f} ₽\n\n"
            "Что хочешь сделать?"
        )

        buttons = [
            [
                InlineKeyboardButton(text="🔧 Изменить лимит", callback_data=f"limit_set_{category}"),
                InlineKeyboardButton(text="🗑 Сбросить лимит", callback_data=f"limit_reset_{category}")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
        ]

        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                                         parse_mode="Markdown")
        await callback.answer()


@router.callback_query(F.data == "open_report_menu")
async def open_report_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "📈 **Финансовый отчёт**\n\n"
        "Выбери период, за который хочешь увидеть статистику:",
        reply_markup=get_report_period_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.in_({"report_today", "report_week", "report_month", "report_year"}))
async def handle_report(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.now()

    # 1. Определяем даты начала и конца периода
    if callback.data == "report_today":
        start, end = get_day_start_end(now)
    elif callback.data == "report_week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif callback.data == "report_month":
        start, end = get_month_start_end(now)
    elif callback.data == "report_year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    else:
        await callback.answer("Ошибка периода", show_alert=True)
        return

    # 2. Получаем данные
    stats = await get_period_stats(user_id, start, end)

    # 3. Формируем красивый текст
    text = f"📊 **Отчёт: {callback.data.replace('report_', '').replace('_', ' ').title()}**\n\n"
    text += f"🗓 Период: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}\n"
    text += f"💰 Траты: **{stats['total_expense']:,.2f} ₽**\n"
    text += f"💵 Пополнения: **{stats['total_income']:,.2f} ₽**\n"
    text += f"📉 Сальдо: **{stats['total_income'] - stats['total_expense']:,.2f} ₽**\n\n"

    if stats["categories"]:
        text += "📁 **Топ категорий:**\n"
        for item in stats["categories"][:5]:
            percent = (item["amount"] / stats["total_expense"] * 100) if stats["total_expense"] > 0 else 0
            text += f"- {item['category'].capitalize()}: {item['amount']:,.2f} ₽ ({percent:.1f}%)\n"
    else:
        text += "🤫 За этот период нет трат."

    await callback.message.edit_text(
        text,
        reply_markup=get_report_period_keyboard(),
        parse_mode="Markdown"
    )

    await callback.answer()


@router.callback_query(F.data == "report_custom")
async def report_custom_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReportPeriodState.waiting_for_start_date)

    calendar = SimpleCalendar()
    await callback.message.edit_text(
        "📅 **Выберите дату начала периода:**\n\n"
        "Используйте стрелки для переключения месяца/года.",
        reply_markup=await calendar.start_calendar(),
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(SimpleCalendarCallback.filter(), ReportPeriodState.waiting_for_start_date)
async def process_start_date(
        callback: CallbackQuery,
        callback_data: SimpleCalendarCallback,
        state: FSMContext
):
    is_selected, selected_date = await SimpleCalendar().process_selection(callback, callback_data)

    if not is_selected:
        await callback.answer()
        return

    await state.update_data(start_date=selected_date)
    await state.set_state(ReportPeriodState.waiting_for_end_date)

    # Открываем второй календарь на месяце выбранной даты
    new_calendar = await SimpleCalendar().start_calendar(
        year=selected_date.year,
        month=selected_date.month
    )

    await callback.message.edit_text(
        f"✅ Дата начала: {selected_date.strftime('%d.%m.%Y')}\n\n"
        "📅 Теперь выбери дату окончания периода:",
        reply_markup=new_calendar,
        parse_mode="Markdown"
    )
    await callback.answer()



@router.callback_query(SimpleCalendarCallback.filter(), ReportPeriodState.waiting_for_start_date)
async def process_start_date(
        callback: CallbackQuery,
        callback_data: SimpleCalendarCallback,
        state: FSMContext
):
    is_selected, selected_date = await SimpleCalendar().process_selection(callback, callback_data)

    if not is_selected:
        # Листание месяцев/годов — календарь уже перерисовался внутри библиотеки
        await callback.answer()
        return

    # Сохраняем дату начала
    await state.update_data(start_date=selected_date)
    await state.set_state(ReportPeriodState.waiting_for_end_date)

    new_calendar = await SimpleCalendar(selected_date).start_calendar()

    await callback.message.edit_text(
        f"✅ Дата начала: {selected_date.strftime('%d.%m.%Y')}\n\n"
        "📅 Теперь выбери дату окончания периода:",
        reply_markup=new_calendar,
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(SimpleCalendarCallback.filter(), ReportPeriodState.waiting_for_end_date)
async def process_end_date(
        callback: CallbackQuery,
        callback_data: SimpleCalendarCallback,
        state: FSMContext
):
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
        await callback.answer(
            "Дата окончания не может быть раньше даты начала!",
            show_alert=True
        )
        return

    # Формирование отчёта
    user_id = callback.from_user.id
    stats = await get_period_stats(user_id, start_date, end_date)

    text = (
        f"📊 **Отчёт за период:** {start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}\n\n"
        f"💰 Пополнения: {stats.get('total_income', 0):,.2f} ₽\n"
        f"💸 Траты: {stats.get('total_expense', 0):,.2f} ₽\n"
        f"📉 Сальдо: {stats.get('total_income', 0) - stats.get('total_expense', 0):,.2f} ₽\n\n"
        "**Детализация по категориям:**\n"
    )

    categories = stats.get("categories", [])
    if categories:
        for item in categories:
            text += f"   • {item['category'].capitalize()}: {item['amount']:,.2f} ₽\n"
    else:
        text += "   • Нет трат за этот период."

    await callback.message.edit_text(
        text,
        reply_markup=get_report_period_keyboard(),
        parse_mode="Markdown"
    )

    await state.clear()
    await callback.answer()

@router.callback_query(F.data.startswith("limit_reset_"))
async def reset_limit_handler(callback: CallbackQuery):
    category = callback.data.replace("limit_reset_", "")
    user_id = callback.from_user.id

    await reset_category_limit(user_id, category)

    # Текст для уведомления
    text = (
        f"✅ Лимит для категории **«{category.capitalize()}»** сброшен.\n"
        f"Теперь отсчёт трат начинается заново с сегодняшнего дня."
    )
    await callback.answer(text, show_alert=True)

    limits = await get_category_limits(user_id)
    instruction_text = "🎯 **Меню лимитов категорий**\n\nЛимит сброшен. Ниже обновлённый список:"

    buttons_rows = []
    if not limits:
        buttons_rows.append([InlineKeyboardButton(text="⚠️ Лимитов пока нет", callback_data="none")])
    else:
        for item in limits:
            cat = item['category']
            lim = item['limit']
            display_lim = f"{lim:.2f} ₽" if lim is not None else "Не установлен"
            row = [
                InlineKeyboardButton(
                    text=f"☕️ {cat.capitalize()}: {display_lim}",
                    callback_data=f"limit_view_{cat}"
                ),
                InlineKeyboardButton(
                    text="🔧 Изменить",
                    callback_data=f"limit_set_{cat}"
                )
            ]
            buttons_rows.append(row)

    buttons_rows.extend([
        [InlineKeyboardButton(text="➕ Установить лимит для новой категории", callback_data="limit_create_new")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons_rows)

    await callback.message.edit_text(instruction_text, reply_markup=keyboard, parse_mode="Markdown")

