"""Уведомления: менеджерам — о новом заказе, клиенту — о смене статуса.

Ошибки отправки здесь не «роняют» бота: если менеджер ни разу не запускал бота
или клиент его заблокировал, Telegram не даст написать — просто пишем в лог.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

import texts
from config import config
from database import orders as orders_db
from keyboards.admin import admin_order_kb
from utils.order_text import format_admin_order

logger = logging.getLogger(__name__)


async def render_admin_order(order_id: int, from_list: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    """Карточка заказа для менеджера (текст + кнопки статусов) по свежим данным из базы."""
    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)
    return format_admin_order(order, items), admin_order_kb(order, from_list)


async def notify_admins_new_order(bot: Bot, order_id: int) -> None:
    """Отправляет карточку нового заказа каждому менеджеру из ADMIN_IDS."""
    text, markup = await render_admin_order(order_id, from_list=False)
    for admin_id in config.admin_ids:
        try:
            message = await bot.send_message(admin_id, "🔔 " + text, reply_markup=markup)
        except TelegramAPIError as error:
            logger.warning("Не удалось уведомить менеджера %s о заказе %s: %s", admin_id, order_id, error)
            continue
        # Запоминаем сообщение, чтобы потом обновлять в нём статус
        await orders_db.save_notification(order_id, admin_id, message.message_id)


async def refresh_admin_notifications(bot: Bot, order_id: int, skip: tuple[int, int] | None = None) -> None:
    """Обновляет карточку заказа во всех уведомлениях у менеджеров.

    skip — (chat_id, message_id) сообщения, которое уже перерисовано
    (где менеджер нажал кнопку), чтобы не править его второй раз.
    """
    text, markup = await render_admin_order(order_id, from_list=False)
    for row in await orders_db.get_notifications(order_id):
        if skip == (row["chat_id"], row["message_id"]):
            continue
        try:
            await bot.edit_message_text(
                text, chat_id=row["chat_id"], message_id=row["message_id"], reply_markup=markup
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                logger.warning("Не удалось обновить уведомление о заказе %s: %s", order_id, error)
        except TelegramAPIError as error:
            logger.warning("Не удалось обновить уведомление о заказе %s: %s", order_id, error)


async def notify_client_status(bot: Bot, order_id: int) -> None:
    """Сообщает клиенту новый статус заказа."""
    order = await orders_db.get_order(order_id)
    status = order["status"]
    if status == "ready":
        key = "ready_delivery" if order["delivery_type"] == "delivery" else "ready_pickup"
    elif status == "cancelled" and order["paid_at"]:
        key = "cancelled_paid"  # заказ был оплачен онлайн — пишем, что вернём деньги
    else:
        key = status
    template = texts.STATUS_MESSAGES.get(key)
    if template is None:
        return
    try:
        await bot.send_message(order["user_id"], template.format(id=order_id))
    except TelegramAPIError as error:
        logger.warning("Не удалось уведомить клиента о заказе %s: %s", order_id, error)


async def notify_admins_duplicate_payment(
    bot: Bot, order_id: int, amount: int, telegram_charge_id: str, provider_charge_id: str
) -> None:
    """Пришла оплата за заказ, который уже оплачен или отменён, — деньги нужно вернуть вручную."""
    text = (
        f"⚠️ <b>Платёж не засчитан: заказ №{order_id}</b>\n\n"
        "Клиент заплатил за заказ, который уже был оплачен или отменён. "
        "Верните деньги в личном кабинете ЮKassa.\n\n"
        f"Сумма: {amount / 100:.2f} ₽\n"
        f"Платёж ЮKassa: <code>{provider_charge_id}</code>\n"
        f"Платёж Telegram: <code>{telegram_charge_id}</code>"
    )
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as error:
            logger.warning("Не удалось уведомить менеджера %s о повторной оплате: %s", admin_id, error)
