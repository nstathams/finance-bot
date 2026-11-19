from html import escape

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.db_manager import (
    set_category_limit,
    get_category_limits,
    reset_category_limit,
    delete_category_limit,
)
from utils.errors import DatabaseError
from .keyboards import get_main_menu_keyboard, get_cancel_keyboard

router = Router()


class LimitStates(StatesGroup):
    waiting_new_category = State()
    waiting_amount = State()


def build_limits_keyboard(limits) -> InlineKeyboardMarkup:
    rows = []
    if not limits:
        rows.append([InlineKeyboardButton(text="⚠️ Лимитов пока нет", callback_data="limits_empty_info")])
    else:
        for item in limits:
            lim = item["limit"]
            display = f"{lim:.2f} ₽" if lim is not None else "Не установлен"
            rows.append([
                InlineKeyboardButton(
                    text=f"{item['category'].capitalize()}: {display}",
                    callback_data=f"limit_view_{item['id']}",
                ),
                InlineKeyboardButton(text="🔧 Изменить", callback_data=f"limit_set_{item['id']}"),
            ])
    rows.append([InlineKeyboardButton(text="➕ Установить лимит для новой категории", callback_data="limit_create_new")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _find_by_id(limits, limit_id):
    return next((x for x in limits if x["id"] == limit_id), None)


async def _edit_limits_menu(callback: CallbackQuery, text: str):
    limits = await get_category_limits(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=build_limits_keyboard(limits))


@router.callback_query(F.data == "limits_empty_info")
async def limits_empty_info(callback: CallbackQuery):
    await callback.answer("Сначала создай первый лимит — кнопка ниже", show_alert=True)


@router.callback_query(F.data == "open_limits_menu")
async def open_limits_menu(callback: CallbackQuery):
    await _edit_limits_menu(
        callback,
        "🎯 <b>Меню лимитов категорий</b>\n\n"
        "Здесь ты можешь контролировать траты по категориям.\n\n"
        "💡 <b>Как это работает:</b>\n"
        "- Если ты установишь лимит 500 ₽ на «кофе», бот предупредит, "
        "когда траты приблизятся к сумме или превысят её.\n"
        "- Счётчик трат можно сбросить в любой момент.\n\n"
        "👇 Ниже список текущих лимитов и кнопки управления:",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("limit_view_"))
async def view_limit_details(callback: CallbackQuery):
    limit_id = int(callback.data.replace("limit_view_", ""))
    limits = await get_category_limits(callback.from_user.id)
    item = _find_by_id(limits, limit_id)

    if item is None or item["limit"] is None:
        await callback.answer("Лимит не найден — меню устарело. Открой меню заново.", show_alert=True)
        return

    category = item["category"]
    text = (
        f"🎯 <b>Лимит категории: {escape(category.capitalize())}</b>\n\n"
        f"Текущий лимит: {item['limit']:.2f} ₽\n\n"
        "Что хочешь сделать?"
    )
    buttons = [
        [
            InlineKeyboardButton(text="🔧 Изменить лимит", callback_data=f"limit_set_{limit_id}"),
            InlineKeyboardButton(text="🗑 Сбросить счётчик", callback_data=f"limit_reset_{limit_id}"),
        ],
        [InlineKeyboardButton(text="❌ Удалить лимит", callback_data=f"limit_delete_{limit_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="open_limits_menu")],
    ]
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("limit_set_"))
async def start_set_limit(callback: CallbackQuery, state: FSMContext):
    limit_id = int(callback.data.replace("limit_set_", ""))
    limits = await get_category_limits(callback.from_user.id)
    item = _find_by_id(limits, limit_id)
    if item is None:
        await callback.answer("Лимит не найден — меню устарело", show_alert=True)
        return

    await state.update_data(category=item["category"])
    await state.set_state(LimitStates.waiting_amount)
    await callback.message.answer(
        f"💵 Установка лимита для категории <b>«{escape(item['category'].capitalize())}»</b>.\n\n"
        "Введи желаемую сумму лимита (только число).\n"
        "Например: <code>3000</code> или <code>150.75</code>",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "limit_create_new")
async def create_new_limit_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(LimitStates.waiting_new_category)
    await callback.message.answer(
        "📝 Создание лимита для новой категории.\n\n"
        "Введи название категории (например, «такси», «одежда»):",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(LimitStates.waiting_new_category, F.text)
async def process_new_category(message: Message, state: FSMContext):
    category = message.text.strip().lower()
    if not category:
        await message.answer("Название категории не может быть пустым. Введи название:")
        return
    await state.update_data(category=category)
    await state.set_state(LimitStates.waiting_amount)
    await message.answer(
        f'Категория установлена: <b>«{escape(category)}»</b>. '
        "Теперь введи лимит (число, например 5000):"
    )


@router.message(LimitStates.waiting_amount, F.text)
async def process_limit_amount(message: Message, state: FSMContext):
    raw = message.text.strip().replace(",", ".")
    try:
        limit_val = float(raw)
    except ValueError:
        await message.answer("⚠️ Введи только число (например, 5000 или 1200.50). Не пиши слова:")
        return
    if limit_val <= 0:
        await message.answer("⚠️ Лимит должен быть положительным числом. Введи ещё раз:")
        return

    category = (await state.get_data()).get("category", "")
    try:
        await set_category_limit(message.from_user.id, category, limit_val)
    except DatabaseError:
        await message.answer("⚠️ Не удалось сохранить лимит. Попробуй позже.")
        return

    await state.clear()
    await message.answer(
        f"✅ Лимит для <b>«{escape(category.capitalize())}»</b> установлен: "
        f"<b>{limit_val:.2f} ₽</b>.\nВозвращаю в главное меню.",
        reply_markup=get_main_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("limit_reset_"))
async def reset_limit_handler(callback: CallbackQuery):
    limit_id = int(callback.data.replace("limit_reset_", ""))
    user_id = callback.from_user.id
    limits = await get_category_limits(user_id)
    item = _find_by_id(limits, limit_id)
    if item is None:
        await callback.answer("Лимит не найден — меню устарело", show_alert=True)
        return

    try:
        await reset_category_limit(user_id, item["id"])
    except DatabaseError:
        await callback.answer("Ошибка сброса, попробуй позже", show_alert=True)
        return

    await callback.answer(
        f"Счётчик трат для «{escape(item['category'].capitalize())}» сброшен — отсчёт с сегодняшнего дня",
        show_alert=True,
    )
    await _edit_limits_menu(
        callback,
        "🎯 <b>Меню лимитов категорий</b>\n\nСчётчик сброшен. Обновлённый список:",
    )


@router.callback_query(F.data.startswith("limit_delete_"))
async def delete_limit_handler(callback: CallbackQuery):
    limit_id = int(callback.data.replace("limit_delete_", ""))
    user_id = callback.from_user.id

    try:
        deleted = await delete_category_limit(user_id, limit_id)
    except DatabaseError:
        await callback.answer("Ошибка удаления, попробуй позже", show_alert=True)
        return

    if not deleted:
        await callback.answer("Лимит не найден — возможно, уже удалён", show_alert=True)
        return

    await callback.answer("Лимит удалён")
    await _edit_limits_menu(
        callback,
        "🗑 <b>Лимит удалён.</b>\n\nПредупреждения по этой категории больше приходить не будут.\n\n"
        "Актуальный список лимитов:",
    )
