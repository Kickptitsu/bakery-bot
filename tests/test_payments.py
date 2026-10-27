"""Оплата картой через Telegram Payments: счёт, проверка перед списанием, повторная оплата."""

import pytest

from conftest import ADMIN, CLIENT, admin_notification, texts
from database import cart as cart_db

CROISSANT = 5   # 120 ₽
PIROZHOK = 8    # 70 ₽ — меньше минимума Telegram

PAYLOAD = "order:1001"
AMOUNT = 24000  # 2 круассана = 240 ₽ = 24000 копеек


@pytest.fixture
def card_payments(settings):
    settings(payment_token="381764678:TEST:fake")


async def order_online(bakery) -> list:
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    await bakery.checkout(CLIENT, payment="💳 Картой в Telegram")
    return await bakery.press(CLIENT, "checkout:confirm")


async def test_no_payment_step_without_token(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    summary = await bakery.checkout(CLIENT)
    assert "Оплата: при получении" in summary[-1].text


async def test_small_order_has_no_card_option(bakery, card_payments):
    await cart_db.change_quantity(CLIENT, PIROZHOK, 1)
    summary = await bakery.checkout(CLIENT)
    assert "Проверьте заказ" in summary[-1].text   # шаг оплаты пропущен


async def test_paid_order_reaches_manager_only_after_payment(bakery, card_payments):
    calls = await order_online(bakery)

    invoice = next(call for call in calls if call.kind == "invoice").method
    assert invoice.payload == PAYLOAD and invoice.start_parameter == "order1001"
    assert [(price.label, price.amount) for price in invoice.prices] == [("Круассан сливочный × 2", AMOUNT)]
    assert texts(calls, "send", ADMIN) == []      # пока не оплачен — менеджер не видит
    assert bakery.db("SELECT status FROM orders") == [("awaiting_payment",)]

    assert (await bakery.precheckout(CLIENT, PAYLOAD, AMOUNT)).alert is True
    calls = await bakery.pay(CLIENT, PAYLOAD, AMOUNT)

    assert "Оплата прошла" in calls[0].text
    assert "✅ оплачен онлайн" in admin_notification(calls).text
    assert bakery.db("SELECT status, paid_at IS NOT NULL FROM orders") == [("new", 1)]


@pytest.mark.parametrize("case, expected_error", [
    ("wrong_amount", "Сумма счёта не совпадает"),
    ("other_user", "Заказ не найден"),
    ("sold_out", "Закончилось: Круассан сливочный"),
    ("price_changed", "Цены изменились"),
])
async def test_precheckout_rejects(bakery, card_payments, case, expected_error):
    await order_online(bakery)
    user, amount = CLIENT, AMOUNT
    if case == "wrong_amount":
        amount = 99900
    elif case == "other_user":
        user = 777
    elif case == "sold_out":
        bakery.db("UPDATE products SET is_available = 0 WHERE id = ?", CROISSANT)
    elif case == "price_changed":
        bakery.db("UPDATE products SET price = 130 WHERE id = ?", CROISSANT)

    answer = await bakery.precheckout(user, PAYLOAD, amount)
    assert answer.alert is False and expected_error in answer.text


async def test_second_payment_is_not_counted(bakery, card_payments):
    await order_online(bakery)
    await bakery.pay(CLIENT, PAYLOAD, AMOUNT, charge="1")

    assert "уже оплачен" in (await bakery.precheckout(CLIENT, PAYLOAD, AMOUNT)).text
    # Если второй платёж всё же прошёл — не засчитываем, просим менеджера вернуть деньги
    calls = await bakery.pay(CLIENT, PAYLOAD, AMOUNT, charge="2")
    assert "не засчитан" in calls[0].text
    assert any("Верните деньги" in text for text in texts(calls, "send", ADMIN))
    assert bakery.db("SELECT provider_charge_id, accepted FROM payments ORDER BY id") == [("yk-1", 1), ("yk-2", 0)]


async def test_cancel_unpaid_order_returns_items_to_cart(bakery, card_payments):
    calls = await order_online(bakery)
    invoice = next(call for call in calls if call.kind == "invoice")

    calls = await bakery.press(CLIENT, "pay:1001:cancel", invoice.message_id)
    assert any(call.kind == "delete" and call.message_id == invoice.message_id for call in calls)
    assert bakery.db("SELECT status FROM orders") == [("cancelled",)]
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 2
    assert "отменён" in (await bakery.precheckout(CLIENT, PAYLOAD, AMOUNT)).text


async def test_invoice_rejected_by_telegram_cancels_order(bakery, card_payments):
    bakery.telegram.fail_invoice = True
    calls = await order_online(bakery)

    assert any("Не получилось выставить счёт" in text for text in texts(calls, "send"))
    assert bakery.db("SELECT status FROM orders") == [("cancelled",)]
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 2


async def test_manager_sees_refund_hint_when_cancelling_paid_order(bakery, card_payments):
    await order_online(bakery)
    await bakery.pay(CLIENT, PAYLOAD, AMOUNT)

    ask = await bakery.press(ADMIN, "adm_order:1001:cancel_ask:0")
    assert "верните деньги" in ask[-1].text and "yk-1" in ask[-1].text
    calls = await bakery.press(ADMIN, "adm_status:1001:cancelled:0")
    assert "Деньги за онлайн-оплату вернём" in texts(calls, "send", CLIENT)[0]
