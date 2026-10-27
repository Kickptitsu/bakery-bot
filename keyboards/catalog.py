"""Inline-кнопки каталога и карточки товара."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import Row

from keyboards.callbacks import CATALOG, NOOP, CartCB, CategoryCB, ProductCB, QtyCB
from utils.formatting import format_price


def categories_kb(categories: list[Row]) -> InlineKeyboardMarkup:
    """Список категорий, по одной в строке."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category["name"], callback_data=CategoryCB(id=category["id"]))
    builder.button(text="🛒 Корзина", callback_data=CartCB(action="open"))
    builder.adjust(1)
    return builder.as_markup()


def products_kb(products: list[Row], in_cart: dict[int, int]) -> InlineKeyboardMarkup:
    """Товары категории. Если товар уже в корзине — показываем сколько."""
    builder = InlineKeyboardBuilder()
    for product in products:
        text = f"{product['name']} — {format_price(product['price'])}"
        if quantity := in_cart.get(product["id"]):
            text += f" · 🛒{quantity}"
        builder.button(text=text, callback_data=ProductCB(id=product["id"]))
    builder.button(text="⬅️ Категории", callback_data=CATALOG)
    builder.button(text="🛒 Корзина", callback_data=CartCB(action="open"))
    # Товары по одному в строке, две нижние кнопки — рядом
    builder.adjust(*[1] * len(products), 2)
    return builder.as_markup()


def product_card_kb(product: Row, quantity: int) -> InlineKeyboardMarkup:
    """Карточка товара: [➖] [2 шт] [➕], корзина и «назад»."""
    builder = InlineKeyboardBuilder()
    product_id = product["id"]

    if quantity == 0:
        builder.button(
            text="➕ Добавить в корзину",
            callback_data=QtyCB(product_id=product_id, delta=1, source="card"),
        )
        first_row = 1
    else:
        builder.button(text="➖", callback_data=QtyCB(product_id=product_id, delta=-1, source="card"))
        builder.button(text=f"{quantity} шт", callback_data=NOOP)
        builder.button(text="➕", callback_data=QtyCB(product_id=product_id, delta=1, source="card"))
        first_row = 3

    builder.button(text="⬅️ Назад", callback_data=CategoryCB(id=product["category_id"]))
    builder.button(text="🛒 Корзина", callback_data=CartCB(action="open"))
    builder.adjust(first_row, 2)
    return builder.as_markup()
