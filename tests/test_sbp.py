"""Оплата по СБП через API ЮKassa (API подменено имитацией — фикстура yookassa)."""

from conftest import ADMIN, CLIENT, admin_notification, texts
from database import cart as cart_db
from services import sbp

CROISSANT = 5   # 120 ₽
PIROZHOK = 8    # 70 ₽


async def order_sbp(bakery) -> list:
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    await bakery.checkout(CLIENT, payment="⚡ По СБП")
    return await bakery.press(CLIENT, "checkout:confirm")


async def poll(bakery) -> list:
    """Один проход фоновой проверки оплат (в работающем боте — раз в 5 секунд)."""
    start = len(bakery.telegram.calls)
    await sbp.check_pending(bakery.bot)
    return bakery.telegram.calls[start:]


async def test_sbp_offered_even_for_small_orders(bakery, yookassa):
    await cart_db.change_quantity(CLIENT, PIROZHOK, 1)
    question = await bakery.checkout(CLIENT)
    assert "⚡ По СБП" in question[-1].buttons()


async def test_paid_by_link_reaches_manager(bakery, yookassa):
    calls = await order_sbp(bakery)

    link = next(call for call in calls if call.kind == "send" and "Оплата заказа №1001 по СБП" in call.text)
    assert link.markup.inline_keyboard[0][0].url == "https://yoomoney.test/pay-1"
    assert yookassa.created[0]["method"] == "bank_card"   # тестовый магазин: СБП нет, проверяем на карте
    assert yookassa.created[0]["return_url"] == "https://t.me/bakery_test_bot"
    assert texts(calls, "send", ADMIN) == []

    assert await poll(bakery) == []   # клиент ещё не заплатил — ничего не происходит

    yookassa.set_status("pay-1", "succeeded")
    calls = await poll(bakery)
    assert "Оплата прошла" in texts(calls, "send", CLIENT)[0]
    assert "✅ оплачен по СБП" in admin_notification(calls).text
    assert bakery.db("SELECT status, payment_method FROM orders") == [("new", "sbp")]

    assert await poll(bakery) == []   # повторно тот же платёж не обрабатывается


async def test_live_key_creates_real_sbp_payment(bakery, yookassa, settings):
    settings(yookassa_secret_key="live_fake")
    calls = await order_sbp(bakery)
    assert yookassa.created[0]["method"] == "sbp"
    assert not any("Тестовый режим" in text for text in texts(calls, "send"))


async def test_link_is_reused_while_alive(bakery, yookassa):
    await order_sbp(bakery)
    await bakery.press(CLIENT, "pay:1001:invoice")
    assert len(yookassa.created) == 1


async def test_canceled_by_yookassa_lets_client_retry(bakery, yookassa):
    await order_sbp(bakery)
    yookassa.set_status("pay-1", "canceled", reason="expired_on_confirmation")

    calls = await poll(bakery)
    assert "истекло время на оплату" in texts(calls, "send", CLIENT)[0]
    assert bakery.db("SELECT status FROM orders") == [("awaiting_payment",)]

    await bakery.press(CLIENT, "pay:1001:invoice")
    assert len(yookassa.created) == 2   # старая ссылка отменена — создан новый платёж


async def test_payment_after_cancel_is_flagged_for_refund(bakery, yookassa):
    await order_sbp(bakery)
    await bakery.press(CLIENT, "pay:1001:cancel")
    yookassa.set_status("pay-1", "succeeded")

    calls = await poll(bakery)
    assert "не засчитан" in texts(calls, "send", CLIENT)[0]
    assert any("Верните деньги" in text for text in texts(calls, "send", ADMIN))
    assert bakery.db("SELECT accepted FROM payments") == [(0,)]


async def test_yookassa_error_cancels_order(bakery, yookassa):
    yookassa.fail = True
    calls = await order_sbp(bakery)
    assert any("Не получилось выставить счёт" in text for text in texts(calls, "send"))
    assert bakery.db("SELECT status FROM orders") == [("cancelled",)]
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 2
