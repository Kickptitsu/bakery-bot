"""Текст заказа: одинаковый в сводке перед подтверждением, в «Моих заказах»
и в карточке заказа у менеджера."""

from collections.abc import Iterable, Mapping
from html import escape

from database.orders import STATUS_LABELS
from database.privacy import ANONYMOUS_USER_ID
from texts import PICKUP_ADDRESS
from utils.formatting import format_datetime, format_price


def format_admin_order(order: Mapping, items: Iterable[Mapping]) -> str:
    """Карточка заказа для менеджера: номер, время, статус, ссылка на клиента и состав."""
    if order["user_id"] == ANONYMOUS_USER_ID:
        client = "🔐 Данные клиента обезличены по его запросу"
    else:
        client = f'💬 <a href="tg://user?id={order["user_id"]}">Написать клиенту в Telegram</a>'
    return (
        f"<b>Заказ №{order['id']}</b> от {format_datetime(order['created_at'])}\n"
        f"Статус: <b>{STATUS_LABELS[order['status']]}</b>\n"
        f"{client}\n\n"
        f"{format_order_body(order, items)}"
    )


def _field(info: Mapping, key: str, default=None):
    """info[key] или default. У строк из базы (aiosqlite.Row) нет метода .get()."""
    return info[key] if key in info.keys() else default


# Способ оплаты -> (значок, как назвать способ, как назвать оплаченный заказ)
PAYMENT_NAMES = {
    "online": ("💳", "онлайн картой", "оплачен онлайн"),
    "sbp": ("⚡", "по СБП", "оплачен по СБП"),
}


def format_payment(info: Mapping) -> str:
    """Строка «Оплата: …» — видно, оплачен ли заказ."""
    method = _field(info, "payment_method", "cash")
    if method not in PAYMENT_NAMES:
        return "💵 Оплата: при получении"
    icon, name, paid_name = PAYMENT_NAMES[method]
    if _field(info, "paid_at"):
        return f"{icon} Оплата: <b>✅ {paid_name}</b>"
    status = _field(info, "status")
    if status == "awaiting_payment":
        return f"{icon} Оплата: {name} — ⏳ ждёт оплаты"
    if status == "cancelled":
        return f"{icon} Оплата: {name} — не оплачен"
    return f"{icon} Оплата: {name}"  # сводка перед подтверждением: заказа ещё нет


def format_order_body(info: Mapping, items: Iterable[Mapping]) -> str:
    """Контакты, способ получения, состав и суммы.

    info  — строка заказа из базы или словарь с теми же ключами
            (customer_name, phone, delivery_type, address, comment,
            items_total, delivery_price, total).
    items — товары с ключами name, price, quantity.
    """
    lines = [
        f"👤 Имя: {escape(info['customer_name'])}",
        f"📞 Телефон: {escape(info['phone'])}",
    ]
    if info["delivery_type"] == "delivery":
        lines.append(f"🚚 Доставка: {escape(info['address'] or '')}")
    else:
        lines.append(f"🏃 Самовывоз: {PICKUP_ADDRESS}")
    if info["comment"]:
        lines.append(f"💬 Комментарий: {escape(info['comment'])}")
    lines.append(format_payment(info))

    lines += ["", "<b>Состав:</b>"]
    for item in items:
        subtotal = item["price"] * item["quantity"]
        lines.append(f"• {escape(item['name'])} × {item['quantity']} = {format_price(subtotal)}")

    lines += ["", f"Товары: {format_price(info['items_total'])}"]
    if info["delivery_type"] == "delivery":
        delivery = format_price(info["delivery_price"]) if info["delivery_price"] else "бесплатно"
        lines.append(f"Доставка: {delivery}")
    lines.append(f"<b>Итого: {format_price(info['total'])}</b>")
    return "\n".join(lines)
