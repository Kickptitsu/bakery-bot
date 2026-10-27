"""Помощник для «перерисовки» сообщения с inline-кнопками."""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

# Функция без аргументов, которая собирает экран: возвращает (текст, кнопки)
Render = Callable[[], Awaitable[tuple[str, InlineKeyboardMarkup | None]]]

# Ключ — (id чата, id сообщения). Блокировка не даёт двум перерисовкам
# одного сообщения идти одновременно и обгонять друг друга.
_locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
# Номер последнего запроса на перерисовку каждого сообщения
_latest: dict[tuple[int, int], int] = {}


def fixed(text: str, markup: InlineKeyboardMarkup | None = None) -> Render:
    """Render для экрана, который не зависит от базы: show(callback, fixed("Готово"))."""
    async def render() -> tuple[str, InlineKeyboardMarkup | None]:
        return text, markup
    return render


async def show(callback: CallbackQuery, render: Render) -> None:
    """Меняет текст и кнопки сообщения, на кнопку которого нажал пользователь.

    Так каталог и корзина живут в одном сообщении, а не засоряют чат.

    Экран собирается функцией render уже в момент перерисовки, поэтому
    показываются самые свежие данные. Если пользователь быстро жмёт ➕,
    промежуточные перерисовки пропускаются — рисуется только последняя.
    """
    message = callback.message
    if not isinstance(message, Message):
        # Сообщение слишком старое, Telegram его не отдаёт — шлём новое
        text, markup = await render()
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
        return

    key = (message.chat.id, message.message_id)
    ticket = _latest.get(key, 0) + 1
    _latest[key] = ticket

    async with _locks[key]:
        if _latest[key] != ticket:
            # Пока ждали очереди, пришло нажатие новее — оно и перерисует
            return
        await _edit(callback, message, render)

    # Больше никто не ждёт — убираем записи, чтобы словари не росли бесконечно
    if _latest.get(key) == ticket:
        del _latest[key]
        _locks.pop(key, None)


async def _edit(callback: CallbackQuery, message: Message, render: Render) -> None:
    text, markup = await render()
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramRetryAfter as error:
        # Слишком частые правки — Telegram просит подождать. Ждём и рисуем заново
        await asyncio.sleep(error.retry_after)
        text, markup = await render()
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        # Содержимое не изменилось — это не ошибка
        if "message is not modified" in str(error):
            return
        # Сообщение нельзя отредактировать (например, удалено) — шлём новое
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
