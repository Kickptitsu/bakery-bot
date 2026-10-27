"""Оплата по СБП через API ЮKassa.

Чем отличается от оплаты картой в Telegram (services/payments.py):
- там счёт и форму оплаты показывает сам Telegram, а бот получает готовое
  сообщение successful_payment;
- здесь бот сам создаёт платёж в ЮKassa и присылает клиенту ссылку. Сообщить боту
  об оплате ЮKassa может только через свой сервер (webhook), а бот работает без
  публичного адреса — поэтому он сам раз в POLL_SECONDS секунд спрашивает статус.

Порядок:
1. send_payment_link() — создаём платёж (или берём ещё живую ссылку) и шлём кнопку.
2. watch_payments() — фоновая задача, запускается в bot.py: опрашивает ЮKassa.
3. succeeded -> заказ оплачен, уходит менеджеру; canceled -> пишем клиенту, заказ ждёт.
"""

import asyncio
import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import texts
from database import orders as orders_db
from database import sbp as sbp_db
from keyboards.callbacks import PayCB
from keyboards.main_menu import main_menu_kb
from services import yookassa_api
from services.notifications import notify_admins_duplicate_payment, notify_admins_new_order
from services.payments import delete_invoice
from utils.formatting import format_price
from utils.order_text import format_order_body

logger = logging.getLogger(__name__)

POLL_SECONDS = 5

# Причины отмены платежа от ЮKassa -> понятный текст для клиента
CANCEL_REASONS = {
    "expired_on_confirmation": " — истекло время на оплату",
    "insufficient_funds": " — недостаточно средств",
    "general_decline": " — банк отклонил платёж",
}


def is_enabled() -> bool:
    return yookassa_api.is_configured()


def _link_kb(order_id: int, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Оплатить через СБП", url=url)],
        [InlineKeyboardButton(text="❌ Отменить заказ", callback_data=PayCB(order_id=order_id, action="cancel").pack())],
    ])


async def send_payment_link(bot: Bot, order_id: int) -> bool:
    """Присылает клиенту кнопку оплаты. False — ЮKassa не создала платёж."""
    order = await orders_db.get_order(order_id)

    # Клиент попросил ссылку ещё раз, а прошлая жива — шлём её, второй платёж не создаём
    payment = await sbp_db.get_fresh_pending(order_id)
    if payment:
        url = payment["confirmation_url"]
    else:
        me = await bot.me()  # aiogram запоминает ответ, второй раз в Telegram не ходит
        try:
            created = await yookassa_api.create_payment(
                amount_rub=order["total"],
                description=f"{texts.BAKERY_NAME}: заказ №{order_id}",
                # В тестовом магазине ЮKassa СБП недоступен — проверяем тот же путь на карте
                method="bank_card" if yookassa_api.is_test_shop() else "sbp",
                return_url=f"https://t.me/{me.username}",  # после оплаты банк вернёт клиента в бота
                metadata={"order_id": str(order_id)},
            )
        except yookassa_api.YooKassaError as error:
            logger.warning("ЮKassa не создала платёж для заказа %s: %s", order_id, error)
            return False
        url = created["confirmation"]["confirmation_url"]
        await sbp_db.save_payment(created["id"], order_id, order["user_id"], url)

    text = texts.SBP_LINK.format(id=order_id, total=format_price(order["total"]))
    if yookassa_api.is_test_shop():
        text += texts.SBP_TEST_NOTE
    message = await bot.send_message(order["user_id"], text, reply_markup=_link_kb(order_id, url))

    # Одна актуальная кнопка оплаты на заказ: прошлое сообщение со ссылкой удаляем
    previous = await orders_db.set_invoice_message(order_id, message.message_id)
    if previous:
        await delete_invoice(bot, order["user_id"], previous)
    return True


# ---------- Фоновый опрос статуса ----------

async def watch_payments(bot: Bot) -> None:
    """Бесконечный цикл: раз в POLL_SECONDS проверяем все неоплаченные платежи."""
    logger.info("Слежу за оплатами по СБП (проверка раз в %s с)", POLL_SECONDS)
    while True:
        try:
            await check_pending(bot)
        except Exception:
            # Одна ошибка (например, нет интернета) не должна остановить слежку навсегда
            logger.exception("Ошибка при проверке оплат по СБП")
        await asyncio.sleep(POLL_SECONDS)


async def check_pending(bot: Bot) -> None:
    for payment in await sbp_db.get_all_pending():
        try:
            info = await yookassa_api.get_payment(payment["id"])
        except yookassa_api.YooKassaError as error:
            logger.warning("Не удалось узнать статус платежа %s: %s", payment["id"], error)
            continue
        if info["status"] == "succeeded":
            await _on_succeeded(bot, payment, info)
        elif info["status"] == "canceled":
            await _on_canceled(bot, payment, info)


async def _on_succeeded(bot: Bot, payment, info: dict) -> None:
    if not await sbp_db.finish(payment["id"], "succeeded"):
        return  # этот платёж уже обработан
    order_id, user_id = payment["order_id"], payment["user_id"]
    amount = int(Decimal(info["amount"]["value"]) * 100)  # "240.00" -> 24000 копеек

    accepted = await orders_db.mark_paid(order_id, user_id, amount, "", payment["id"])
    if not accepted:
        # Заказ успели отменить (или уже оплатить), а деньги пришли — их нужно вернуть
        logger.warning("Оплата по СБП не засчитана: заказ %s", order_id)
        await _send(bot, user_id, texts.PAYMENT_DUPLICATE.format(id=order_id))
        await notify_admins_duplicate_payment(bot, order_id, amount, "—", payment["id"])
        return

    # Кнопка «Оплатить» больше не нужна
    link_message = await orders_db.set_invoice_message(order_id, None)
    if link_message:
        await delete_invoice(bot, user_id, link_message)
    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)
    await _send(
        bot, user_id,
        texts.PAYMENT_SUCCESS.format(id=order_id) + "\n\n"
        f"{format_order_body(order, items)}\n\n"
        f"Статус: {orders_db.STATUS_LABELS[order['status']]}. Мы пришлём уведомление, когда он изменится.",
        main_menu_kb(),
    )
    await notify_admins_new_order(bot, order_id)


async def _on_canceled(bot: Bot, payment, info: dict) -> None:
    if not await sbp_db.finish(payment["id"], "canceled"):
        return
    order = await orders_db.get_order(payment["order_id"])
    if order is None or order["status"] != "awaiting_payment":
        return  # заказ уже отменён клиентом — сообщать нечего
    link_message = await orders_db.set_invoice_message(order["id"], None)
    if link_message:
        await delete_invoice(bot, payment["user_id"], link_message)
    reason = CANCEL_REASONS.get(info.get("cancellation_details", {}).get("reason"), "")
    await _send(bot, payment["user_id"], texts.SBP_FAILED.format(id=order["id"], reason=reason))


async def _send(bot: Bot, chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramAPIError as error:
        logger.warning("Не удалось отправить сообщение %s: %s", chat_id, error)
