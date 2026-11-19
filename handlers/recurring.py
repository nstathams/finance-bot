from html import escape

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.db_manager import (
    add_recurring_payment,
    get_recurring_payments,
    delete_recurring_payment,
)
from parsers.transaction_parser import parse_transaction
from utils.errors import InvalidFormatError
from .keyboards import get_cancel_keyboard

router = Router()

FREQ_LABELS = {"daily": "каждый день", "weekly": "каждую неделю", "monthly": "каждый месяц"}


class RecurringStates(StatesGroup):
    waiting_transaction = State()
    waiting_action = State()


def get_recurring_menu_keyboard(payments) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="➕ Добавить платёж", callback_data="recurring_add")]]
    for p in payments:
        freq = FREQ_LABELS.get(p["freq"], p["freq"])
        mode = "🤖 авто" if p["action"] == "auto" else "🔔 напом."
        sign = "-" if p["type"] == "expense" else "+"
        next_run = (p["next_run"] or "")[:10]
        buttons.append([
            InlineKeyboardButton(
                text=f"{sign}{p['amount']:.2f} ₽ {p['category']} ({freq}, с {next_run}) {mode}",
                callback_data=f"recurring_del_{p['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "recurring_menu")
async def recurring_menu_handler(callback: CallbackQuery):
    payments = await get_recurring_payments(callback.from_user.id)
    text = (
        "🔁 <b>Регулярные платежи</b>\n\n"
        "Здесь настраиваются постоянные расходы и доходы: подписки, аренда, зарплата.\n"
        "- 🤖 <b>Автосписание</b> — бот сам добавляет операцию в нужную дату.\n"
        "- 🔔 <b>Напоминание</b> — бот присылает уведомление, операцию добавляешь ты.\n\n"
        "Нажми на платёж, чтобы удалить его:"
    )
    await callback.message.edit_text(text, reply_markup=get_recurring_menu_keyboard(payments))
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
        "🔁 <b>Новый регулярный платёж</b>\n\nВыбери периодичность:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recur_freq_"))
async def recurring_freq_chosen(callback: CallbackQuery, state: FSMContext):
    freq = callback.data.replace("recur_freq_", "")
    await state.update_data(freq=freq)
    await state.set_state(RecurringStates.waiting_transaction)
    await callback.message.edit_text(
        f"🔁 Периодичность: <b>{FREQ_LABELS[freq]}</b>.\n\n"
        "Теперь напиши операцию в привычном формате, например: <code>-500 подписка</code>\n"
        "Знак определяет тип: траты (−) или пополнение (+).",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(RecurringStates.waiting_transaction, F.text)
async def recurring_transaction_input(message: Message, state: FSMContext):
    try:
        transaction = parse_transaction(message.text)
    except InvalidFormatError as e:
        await message.answer(
            f"⚠️ {escape(str(e))}\n\nПопробуй ещё раз в формате: <code>-500 подписка</code>",
            reply_markup=get_cancel_keyboard(),
        )
        return

    await state.update_data(
        amount=transaction.amount,
        category=transaction.category,
        t_type=transaction.t_type,
    )
    await state.set_state(RecurringStates.waiting_action)
    await message.answer(
        f"✅ Понял: {transaction.amount:.2f} ₽, «{escape(transaction.category)}».\n\n"
        "Что делать в дату платежа?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤖 Списать автоматически", callback_data="recur_action_auto")],
            [InlineKeyboardButton(text="🔔 Только напомнить", callback_data="recur_action_remind")],
        ]),
    )


@router.callback_query(F.data.startswith("recur_action_"))
async def recurring_action_chosen(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    freq = data.get("freq")
    amount = data.get("amount")
    category = data.get("category")
    t_type = data.get("t_type")

    if freq is None or amount is None or category is None:
        await callback.answer("Сессия истекла, начни заново", show_alert=True)
        await state.clear()
        return

    action = "auto" if callback.data == "recur_action_auto" else "remind"
    await add_recurring_payment(callback.from_user.id, amount, category, t_type, freq, action)
    await state.clear()

    await callback.message.edit_text(
        f"✅ Платёж «{escape(category)}» на {amount:.2f} ₽ добавлен.\n"
        f"Периодичность: {FREQ_LABELS[freq]}. "
        f"Режим: {'🤖 автосписание' if action == 'auto' else '🔔 напоминание'}.",
        reply_markup=get_recurring_menu_keyboard(await get_recurring_payments(callback.from_user.id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recurring_del_"))
async def recurring_delete_handler(callback: CallbackQuery):
    payment_id = int(callback.data.replace("recurring_del_", ""))
    await delete_recurring_payment(payment_id, callback.from_user.id)
    payments = await get_recurring_payments(callback.from_user.id)
    await callback.message.edit_text(
        "🗑 Платёж удалён. Обновлённый список:",
        reply_markup=get_recurring_menu_keyboard(payments),
    )
    await callback.answer()
