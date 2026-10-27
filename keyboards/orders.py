"""Inline-кнопки раздела «Мои заказы»."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import Row

from database.orders import STATUS_LABELS
from keyboards.callbacks import CATALOG, MY_ORDERS, OrderCB, PayCB
from utils.formatting import format_datetime, format_price


def orders_list_kb(orders: list[Row]) -> InlineKeyboardMarkup:
    """Каждый заказ — кнопка: «№1001 · 25.10 · 1 240 ₽ · 🆕 Новый»."""
    builder = InlineKeyboardBuilder()
    if not orders:
        builder.button(text="🥐 Перейти в каталог", callback_data=CATALOG)
        return builder.as_markup()

    for order in orders:
        date = format_datetime(order["created_at"])[:5]  # только «25.10»
        text = f"№{order['id']} · {date} · {format_price(order['total'])} · {STATUS_LABELS[order['status']]}"
        builder.button(text=text, callback_data=OrderCB(id=order["id"]))
    builder.adjust(1)
    return builder.as_markup()


def order_details_kb(order: Row) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = []
    if order["status"] == "awaiting_payment":
        builder.button(text="💳 Оплатить", callback_data=PayCB(order_id=order["id"], action="invoice"))
        builder.button(text="❌ Отменить заказ", callback_data=PayCB(order_id=order["id"], action="cancel"))
        rows.append(2)
    builder.button(text="⬅️ Все заказы", callback_data=MY_ORDERS)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()
