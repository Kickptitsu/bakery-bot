"""Онлайн-оплата: проверка перед списанием, успешная оплата, повторный счёт и отмена.

Порядок процесса — в начале services/payments.py (картой в Telegram)
и services/sbp.py (по СБП: там оплату отслеживает фоновая задача, а не этот файл).
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

import texts
from database import orders as orders_db
from handlers.orders import render_order
from keyboards.callbacks import PayCB
from keyboards.main_menu import main_menu_kb
from services import payments, sbp
from services.notifications import notify_admins_duplicate_payment, notify_admins_new_order
from utils.messages import show
from utils.order_text import format_order_body

logger = logging.getLogger(__name__)

router = Router(name="payments")


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    """Telegram спрашивает: «можно списывать?». Ответить нужно за 10 секунд."""
    error = await payments.check_before_payment(query)
    if error:
        logger.info("Оплата отклонена до списания (%s): %s", query.invoice_payload, error)
    await query.answer(ok=error is None, error_message=error)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    """Деньги списаны. Засчитываем оплату и только теперь отправляем заказ менеджеру."""
    payment = message.successful_payment
    user_id = message.from_user.id
    order_id = payments.parse_payload(payment.invoice_payload)

    accepted = order_id is not None and await orders_db.mark_paid(
        order_id,
        user_id,
        payment.total_amount,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
    )
    if not accepted:
        # Сюда попадаем, только если заказ успели оплатить или отменить, пока шла эта оплата.
        # Деньги уже списаны — честно говорим клиенту и просим менеджера вернуть их.
        logger.warning("Повторная оплата заказа %s от пользователя %s", order_id, user_id)
        await message.answer(texts.PAYMENT_DUPLICATE.format(id=order_id))
        await notify_admins_duplicate_payment(
            message.bot, order_id, payment.total_amount,
            payment.telegram_payment_charge_id, payment.provider_payment_charge_id,
        )
        return

    await orders_db.set_invoice_message(order_id, None)  # счёт оплачен — удалять его не нужно
    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)
    await message.answer(
        texts.PAYMENT_SUCCESS.format(id=order_id) + "\n\n"
        f"{format_order_body(order, items)}\n\n"
        f"Статус: {orders_db.STATUS_LABELS[order['status']]}. Мы пришлём уведомление, когда он изменится.",
        reply_markup=main_menu_kb(),
    )
    await notify_admins_new_order(message.bot, order_id)


async def _get_own_unpaid_order(callback: CallbackQuery, order_id: int):
    """Заказ клиента, который ждёт оплаты, или None (с объяснением во всплывающем окне)."""
    order = await orders_db.get_order(order_id)
    if order is None or order["user_id"] != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return None
    if order["status"] != "awaiting_payment":
        text = "Заказ уже оплачен" if order["paid_at"] else "Заказ уже не ждёт оплаты"
        await callback.answer(text, show_alert=True)
        return None
    return order


@router.callback_query(PayCB.filter(F.action == "invoice"))
async def resend_invoice(callback: CallbackQuery, callback_data: PayCB) -> None:
    """«💳 Оплатить» в «Моих заказах»: присылаем свежий счёт (или ссылку СБП), старый удаляется."""
    order = await _get_own_unpaid_order(callback, callback_data.order_id)
    if order is None:
        return
    await callback.answer()
    if order["payment_method"] == "sbp":
        sent = await sbp.send_payment_link(callback.bot, callback_data.order_id)
    else:
        sent = await payments.send_invoice(callback.bot, callback_data.order_id)
    if not sent:
        await orders_db.cancel_unpaid(callback_data.order_id, callback.from_user.id)
        await callback.bot.send_message(
            callback.from_user.id, texts.PAYMENT_UNAVAILABLE.format(id=callback_data.order_id)
        )


@router.callback_query(PayCB.filter(F.action == "cancel"))
async def cancel_unpaid_order(callback: CallbackQuery, callback_data: PayCB) -> None:
    """Клиент отменяет неоплаченный заказ (под счётом или в «Моих заказах»)."""
    order_id = callback_data.order_id
    order = await _get_own_unpaid_order(callback, order_id)
    if order is None:
        return
    if not await orders_db.cancel_unpaid(order_id, callback.from_user.id):
        await callback.answer("Заказ уже не ждёт оплаты", show_alert=True)
        return
    await callback.answer("Заказ отменён")

    # Удаляем счёт (или ссылку СБП), чтобы по нему нельзя было заплатить
    invoice_id = await orders_db.set_invoice_message(order_id, None)
    if invoice_id:
        await payments.delete_invoice(callback.bot, callback.from_user.id, invoice_id)
    # Нажали в «Моих заказах» (а не под счётом) — перерисовываем карточку заказа
    if callback.message and callback.message.message_id != invoice_id:
        await show(callback, lambda: render_order(order_id))
    await callback.bot.send_message(
        callback.from_user.id, texts.PAYMENT_CANCELLED.format(id=order_id), reply_markup=main_menu_kb()
    )
