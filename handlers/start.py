"""Команда /start и раздел «О нас»."""

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import texts
from keyboards.main_menu import BTN_ABOUT, main_menu_kb

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    # Если пользователь был посреди какого-то сценария — сбрасываем его
    await state.clear()
    name = escape(message.from_user.first_name or "друг")
    await message.answer(texts.WELCOME.format(name=name), reply_markup=main_menu_kb())


@router.message(F.text == BTN_ABOUT)
async def about(message: Message) -> None:
    await message.answer(texts.ABOUT, reply_markup=main_menu_kb())
