"""Inline-кнопки корзины."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import Row

from keyboards.callbacks import CATALOG, NOOP, CartCB, QtyCB

# Длинные названия не влезают в кнопку на телефоне — обрезаем
MAX_NAME_LEN = 18


def _short(name: str) -> str:
    return name if len(name) <= MAX_NAME_LEN else name[: MAX_NAME_LEN - 1] + "…"


def cart_kb(items: list[Row]) -> InlineKeyboardMarkup:
    """Для каждого товара строка [➖] [Название ×2] [➕], внизу — действия с корзиной."""
    builder = InlineKeyboardBuilder()

    if not items:
        builder.button(text="🥐 Перейти в каталог", callback_data=CATALOG)
        return builder.as_markup()

    for item in items:
        product_id = item["product_id"]
        builder.button(text="➖", callback_data=QtyCB(product_id=product_id, delta=-1, source="cart"))
        builder.button(text=f"{_short(item['name'])} ×{item['quantity']}", callback_data=NOOP)
        builder.button(text="➕", callback_data=QtyCB(product_id=product_id, delta=1, source="cart"))

    builder.button(text="🗑 Очистить", callback_data=CartCB(action="clear"))
    builder.button(text="✅ Оформить заказ", callback_data=CartCB(action="checkout"))
    builder.button(text="🥐 Продолжить покупки", callback_data=CATALOG)
    builder.adjust(*[3] * len(items), 2, 1)
    return builder.as_markup()
