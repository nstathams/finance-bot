import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from parsers.transaction_parser import parse_transaction
from database.db_manager import add_transaction, get_balance
from utils.errors import InvalidFormatError, DatabaseError
from .keyboards import get_main_menu_keyboard, get_cancel_keyboard, make_main_menu_text

router = Router()


class AddOperationState(StatesGroup):
    waiting_transaction = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.first_name or "Пользователь"
    balance = await get_balance(user_id)
    await message.answer(
        make_main_menu_text(username, balance),
        reply_markup=get_main_menu_keyboard(),
    )


# Универсальная отмена: сбрасывает любой диалог и возвращает в меню
@router.callback_query(F.data == "cancel_action")
async def cancel_action_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    balance = await get_balance(user_id)
    username = callback.from_user.first_name or "Пользователь"
    try:
        await callback.message.edit_text(
            make_main_menu_text(username, balance),
            reply_markup=get_main_menu_keyboard(),
        )
    except TelegramBadRequest:
        pass
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "start_add_transaction")
async def start_add_transaction_handler(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddOperationState.waiting_transaction)
    text = (
        "📝 <b>Как добавить операцию:</b>\n\n"
        "Сначала знак (+ или −), потом число, потом категория.\n"
        "✅ Примеры: <code>-300 кофе</code>, <code>+50000 зарплата</code>.\n\n"
        "👇 Напиши сообщение в таком формате:"
    )
    await callback.message.edit_text(text, reply_markup=get_cancel_keyboard())
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


def _save_result(result):
    if isinstance(result, tuple):
        return result
    return True, None


def _format_response(transaction, balance, warning: str | None) -> str:
    emoji = "📉" if transaction.t_type == "expense" else "📈"
    response = (
        f"{emoji} Операция сохранена!\n"
        f"Категория: {transaction.category.capitalize()}\n"
        f"Сумма: {transaction.amount:.2f} ₽\n"
        f"Твой баланс: {balance:.2f} ₽"
    )
    if warning:
        response += "\n\n" + warning
    return response


@router.message(AddOperationState.waiting_transaction, F.text)
async def process_transaction_input(message: Message, state: FSMContext):
    try:
        transaction = parse_transaction(message.text)
    except InvalidFormatError as e:
        await message.answer(str(e))
        return

    user_id = message.from_user.id
    try:
        warning = await add_transaction(user_id, transaction.amount,
                                        transaction.category, transaction.t_type)
    except DatabaseError as e:
        logging.exception("Ошибка сохранения транзакции: %s", e)
        await message.answer("⚠️ Не удалось сохранить операцию. Попробуй позже.")
        return

    balance = await get_balance(user_id)
    await state.clear()
    await message.answer(
        _format_response(transaction, balance, warning),
        reply_markup=get_main_menu_keyboard(),
    )

@router.message(StateFilter(None), F.text)
async def quick_transaction(message: Message, state: FSMContext):
    if await state.get_state() is not None:
        return

    try:
        transaction = parse_transaction(message.text)
    except InvalidFormatError:
        return

    user_id = message.from_user.id
    try:
        warning = await add_transaction(user_id, transaction.amount,
                                        transaction.category, transaction.t_type)
    except DatabaseError as e:
        logging.exception("Ошибка сохранения транзакции: %s", e)
        await message.answer("⚠️ Не удалось сохранить операцию. Попробуй позже.")
        return

    balance = await get_balance(user_id)
    emoji = "📉" if transaction.t_type == "expense" else "📈"
    response = f"{emoji} Транзакция сохранена!\nТекущий баланс: {balance:.2f} ₽"
    if warning:
        response += "\n\n" + warning
    await message.answer(response, reply_markup=get_main_menu_keyboard())

