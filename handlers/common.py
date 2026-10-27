"""Ответы «на всё остальное». Этот роутер подключается последним."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import texts
from keyboards.callbacks import NOOP
from keyboards.main_menu import main_menu_kb

router = Router(name="common")


@router.callback_query(F.data == NOOP)
async def noop(callback: CallbackQuery) -> None:
    # Кнопка-надпись (например, «2 шт») — просто убираем «часики» на кнопке
    await callback.answer()


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    # Нажатие на кнопку, которую бот уже не знает (например, после обновления)
    await callback.answer("Кнопка устарела, откройте раздел заново через меню")


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer(texts.UNKNOWN_MESSAGE, reply_markup=main_menu_kb())
