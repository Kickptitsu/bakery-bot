"""Inline-кнопки для менеджера: карточка заказа и панель /admin."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import Row

from database.orders import STATUS_LABELS, TRANSITIONS
from keyboards.callbacks import (
    AdminCB,
    AdminOrderCB,
    AdminStatusCB,
    AdminStockCategoryCB,
    AdminToggleCB,
)
from utils.formatting import format_datetime, format_price

# Подписи кнопок «перевести в статус …»
STATUS_ACTIONS = {
    "cooking": "👩‍🍳 Взять в работу",
    "ready": "✅ Готов",
    "done": "📦 Выполнен",
}


def _back_to_menu(builder: InlineKeyboardBuilder) -> None:
    builder.button(text="⬅️ Меню", callback_data=AdminCB(section="menu"))


# ---------- Карточка заказа ----------

def admin_order_kb(order: Row, from_list: bool) -> InlineKeyboardMarkup | None:
    """Кнопки следующего статуса и «Отменить». У завершённых заказов кнопок статусов нет.

    Если кнопок нет совсем — возвращаем None: тогда Telegram просто уберёт
    клавиатуру из сообщения (пустую клавиатуру лучше не отправлять).
    """
    builder = InlineKeyboardBuilder()
    order_id = order["id"]
    rows = []

    for status in TRANSITIONS.get(order["status"], ()):
        if status == "cancelled":
            # Отмену сначала подтверждаем — её не вернуть
            builder.button(
                text="❌ Отменить заказ",
                callback_data=AdminOrderCB(id=order_id, action="cancel_ask", from_list=from_list),
            )
        else:
            builder.button(
                text=STATUS_ACTIONS[status],
                callback_data=AdminStatusCB(id=order_id, status=status, from_list=from_list),
            )
        rows.append(1)

    if from_list:
        builder.button(text="⬅️ К списку заказов", callback_data=AdminCB(section="orders"))
        rows.append(1)

    if not rows:
        return None
    builder.adjust(*rows)
    return builder.as_markup()


def cancel_confirm_kb(order_id: int, from_list: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Да, отменить заказ",
        callback_data=AdminStatusCB(id=order_id, status="cancelled", from_list=from_list),
    )
    builder.button(
        text="⬅️ Нет, назад",
        callback_data=AdminOrderCB(id=order_id, action="open", from_list=from_list),
    )
    builder.adjust(1)
    return builder.as_markup()


# ---------- Панель /admin ----------

def admin_menu_kb(active_count: int, privacy_waiting: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Активные заказы ({active_count})", callback_data=AdminCB(section="orders"))
    builder.button(text="🥐 Наличие товаров", callback_data=AdminCB(section="stock"))
    builder.button(text="📊 Статистика", callback_data=AdminCB(section="stats"))
    privacy_text = "🔐 Персональные данные"
    if privacy_waiting:
        privacy_text += f" ({privacy_waiting} ждут решения)"
    builder.button(text=privacy_text, callback_data=AdminCB(section="privacy"))
    builder.adjust(1)
    return builder.as_markup()


def active_orders_kb(orders: list[Row]) -> InlineKeyboardMarkup:
    """Активные заказы: «№1001 · 17:40 · 🚚 · 590 ₽ · 🆕 Новый»."""
    builder = InlineKeyboardBuilder()
    for order in orders:
        time = format_datetime(order["created_at"])[-5:]  # только «17:40»
        delivery = "🚚" if order["delivery_type"] == "delivery" else "🏃"
        text = (
            f"№{order['id']} · {time} · {delivery} · "
            f"{format_price(order['total'])} · {STATUS_LABELS[order['status']]}"
        )
        builder.button(text=text, callback_data=AdminOrderCB(id=order["id"], action="open", from_list=True))
    builder.button(text="🔄 Обновить", callback_data=AdminCB(section="orders"))
    _back_to_menu(builder)
    builder.adjust(*[1] * len(orders), 2)
    return builder.as_markup()


def stock_categories_kb(categories: list[Row]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category["name"], callback_data=AdminStockCategoryCB(id=category["id"]))
    _back_to_menu(builder)
    builder.adjust(1)
    return builder.as_markup()


def stock_products_kb(products: list[Row]) -> InlineKeyboardMarkup:
    """Каждый товар — кнопка-переключатель: ✅ в наличии / ❌ нет."""
    builder = InlineKeyboardBuilder()
    for product in products:
        mark = "✅" if product["is_available"] else "❌"
        builder.button(text=f"{mark} {product['name']}", callback_data=AdminToggleCB(product_id=product["id"]))
    builder.button(text="⬅️ Категории", callback_data=AdminCB(section="stock"))
    builder.adjust(1)
    return builder.as_markup()


def stats_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=AdminCB(section="stats"))
    _back_to_menu(builder)
    builder.adjust(2)
    return builder.as_markup()
