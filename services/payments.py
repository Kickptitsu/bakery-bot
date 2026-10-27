"""Онлайн-оплата через Telegram Payments (провайдер ЮKassa).

Как это работает:
1. send_invoice() — бот отправляет счёт с кнопкой «Оплатить». В payload
   счёта лежит номер заказа: "order:1005".
2. Клиент вводит карту в форме Telegram — бот данные карты не видит.
3. Перед списанием Telegram присылает pre_checkout_query —
   check_before_payment() решает, можно ли платить (ответить нужно за 10 секунд).
4. После списания приходит сообщение successful_payment — хендлер в
   handlers/payments.py отмечает заказ оплаченным и отправляет его менеджеру.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery

import texts
from config import config
from database import orders as orders_db
from keyboards.callbacks import PayCB
from utils.formatting import format_price

logger = logging.getLogger(__name__)

CURRENCY = "RUB"
PAYLOAD_PREFIX = "order:"


def is_enabled() -> bool:
    """Онлайн-оплата включена, только если в .env задан PAYMENT_TOKEN."""
    return bool(config.payment_token)


def is_test_mode() -> bool:
    # Тестовые токены ЮKassa от @BotFather выглядят как «123456:TEST:abcdef»
    return ":TEST:" in config.payment_token


def make_payload(order_id: int) -> str:
    return f"{PAYLOAD_PREFIX}{order_id}"


def parse_payload(payload: str) -> int | None:
    """'order:1005' -> 1005; чужой или испорченный payload -> None."""
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    value = payload[len(PAYLOAD_PREFIX):]
    return int(value) if value.isdigit() else None


def _invoice_kb(order_id: int, total: int) -> InlineKeyboardMarkup:
    # Telegram требует, чтобы кнопка оплаты (pay=True) была первой
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {format_price(total)}", pay=True)],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=PayCB(order_id=order_id, action="cancel").pack())],
    ])


async def send_invoice(bot: Bot, order_id: int) -> bool:
    """Отправляет клиенту счёт на заказ. False — Telegram отказал (например, сумма меньше минимума).

    Если счёт на этот заказ уже отправлялся, старое сообщение со счётом удаляем,
    чтобы у клиента был только один актуальный счёт.
    """
    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)

    # Суммы в Telegram передаются в копейках: 120 ₽ -> 12000
    prices = [
        LabeledPrice(label=f"{item['name']} × {item['quantity']}", amount=item["price"] * item["quantity"] * 100)
        for item in items
    ]
    if order["delivery_price"]:
        prices.append(LabeledPrice(label="Доставка", amount=order["delivery_price"] * 100))

    description = f"{texts.BAKERY_NAME}: " + ", ".join(f"{i['name']} ×{i['quantity']}" for i in items)
    if len(description) > 255:  # ограничение Telegram
        description = description[:254] + "…"

    try:
        message = await bot.send_invoice(
            chat_id=order["user_id"],
            title=f"Заказ №{order_id}",
            description=description,
            payload=make_payload(order_id),
            provider_token=config.payment_token,
            currency=CURRENCY,
            prices=prices,
            # С start_parameter счёт в чате с ботом можно оплатить только один раз,
            # а в пересланной копии вместо «Оплатить» будет ссылка на бота
            start_parameter=f"order{order_id}",
            reply_markup=_invoice_kb(order_id, order["total"]),
        )
    except TelegramBadRequest as error:
        logger.warning("Не удалось отправить счёт на заказ %s: %s", order_id, error)
        return False

    previous = await orders_db.set_invoice_message(order_id, message.message_id)
    if previous:
        await delete_invoice(bot, order["user_id"], previous)
    return True


async def delete_invoice(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass  # сообщение уже удалено или слишком старое — не страшно, оплатить его всё равно не дадим


async def check_before_payment(query: PreCheckoutQuery) -> str | None:
    """Последняя проверка перед списанием денег.

    Возвращает None, если платить можно, или текст ошибки — Telegram покажет
    его клиенту в форме оплаты.
    """
    order_id = parse_payload(query.invoice_payload)
    order = await orders_db.get_order(order_id) if order_id else None
    if order is None or order["user_id"] != query.from_user.id:
        return "Заказ не найден."

    # Повторная оплата невозможна: платить можно только заказ, который ждёт оплаты
    if order["status"] != "awaiting_payment":
        if order["paid_at"]:
            return f"Заказ №{order_id} уже оплачен."
        return f"Заказ №{order_id} отменён, оплатить его нельзя."

    if query.currency != CURRENCY or query.total_amount != order["total"] * 100:
        return "Сумма счёта не совпадает с заказом. Запросите новый счёт в «Мои заказы»."

    items = await orders_db.get_items_with_stock(order_id)
    sold_out = [item["name"] for item in items if not item["is_available"]]
    if sold_out:
        return (
            f"Закончилось: {', '.join(sold_out)}. Отмените заказ в «Мои заказы» — "
            "товары вернутся в корзину, и вы сможете оформить его заново."
        )

    # Сумма по текущим ценам каталога должна совпасть с суммой заказа
    items_total = sum(item["current_price"] * item["quantity"] for item in items)
    current_total = items_total + orders_db.calc_delivery_price(items_total, order["delivery_type"])
    if current_total != order["total"]:
        return (
            "Цены изменились с момента оформления. Отмените заказ в «Мои заказы» "
            "и оформите его заново."
        )
    return None
