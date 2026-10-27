"""Менеджер: доступ только по ADMIN_IDS, статусы заказа, наличие товаров, статистика."""

import asyncio

from conftest import ADMIN, ADMIN2, CLIENT, admin_notification, texts
from database import cart as cart_db

CROISSANT = 5


async def place_order(bakery) -> list:
    """Клиент оформляет заказ на 2 круассана (самовывоз). Возвращает действия бота при подтверждении."""
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    consent_given = bool(bakery.db("SELECT 1 FROM consents WHERE user_id = ?", CLIENT))
    await bakery.checkout(CLIENT, consent=not consent_given)
    return await bakery.press(CLIENT, "checkout:confirm")


async def test_client_has_no_admin_access(bakery):
    await place_order(bakery)
    assert "Не понял" in (await bakery.send(CLIENT, "/admin"))[0].text
    # Даже подделанная кнопка статуса не сработает
    await bakery.press(CLIENT, "adm_status:1001:cooking:0")
    assert bakery.db("SELECT status FROM orders") == [("new",)]


async def test_status_flow_notifies_client_and_all_admins(bakery):
    confirm = await place_order(bakery)
    first, second = admin_notification(confirm, ADMIN), admin_notification(confirm, ADMIN2)

    calls = await bakery.press(ADMIN, "adm_status:1001:cooking:0", first.message_id)

    assert calls[0].kind == "answer"
    assert "Заказ №1001 готовится" in texts(calls, "send", CLIENT)[0]
    # Карточка обновилась и у второго менеджера
    edited = [call for call in calls if call.kind == "edit" and call.message_id == second.message_id]
    assert "Готовится" in edited[0].text

    for status in ("ready", "done"):
        calls = await bakery.press(ADMIN, f"adm_status:1001:{status}:0", first.message_id)
    assert "выполнен" in texts(calls, "send", CLIENT)[0]
    assert bakery.db("SELECT status FROM orders") == [("done",)]


async def test_stale_button_shows_current_status(bakery):
    confirm = await place_order(bakery)
    second = admin_notification(confirm, ADMIN2)
    await bakery.press(ADMIN, "adm_status:1001:cooking:0")

    calls = await bakery.press(ADMIN2, "adm_status:1001:cooking:0", second.message_id)
    assert calls[0].alert and "Статус уже изменён" in calls[0].text


async def test_simultaneous_presses_change_status_once(bakery):
    """Два менеджера одновременно жмут «Взять в работу»: срабатывает одно нажатие."""
    await place_order(bakery)
    start = len(bakery.telegram.calls)
    # При одновременных нажатиях ответы перемешиваются — смотрим общий список действий бота
    await asyncio.gather(
        bakery.press(ADMIN, "adm_status:1001:cooking:0"),
        bakery.press(ADMIN2, "adm_status:1001:cooking:0"),
    )
    calls = bakery.telegram.calls[start:]
    answers = [call for call in calls if call.kind == "answer"]
    assert sorted(bool(answer.alert) for answer in answers) == [False, True]
    assert any("Статус уже изменён" in answer.text for answer in answers)
    # Клиент получил одно уведомление, а не два
    assert len(texts(calls, "send", CLIENT)) == 1


async def test_skipping_a_status_is_impossible(bakery):
    """Из «Новый» сразу в «Готов» нельзя — только по порядку."""
    await place_order(bakery)
    calls = await bakery.press(ADMIN, "adm_status:1001:ready:0")
    assert calls[0].alert
    assert bakery.db("SELECT status FROM orders") == [("new",)]


async def test_cancel_asks_for_confirmation(bakery):
    await place_order(bakery)
    ask = await bakery.press(ADMIN, "adm_order:1001:cancel_ask:0")
    assert "Точно отменить заказ?" in ask[-1].text
    assert bakery.db("SELECT status FROM orders") == [("new",)]

    calls = await bakery.press(ADMIN, "adm_status:1001:cancelled:0")
    assert "отменён" in texts(calls, "send", CLIENT)[0]


async def test_stock_toggle_hides_product_from_catalog(bakery):
    calls = await bakery.press(ADMIN, f"adm_toggle:{CROISSANT}")
    assert "нет в наличии" in calls[0].text

    category = await bakery.press(CLIENT, "cat:2")
    assert not any("Круассан сливочный" in button for button in category[-1].buttons())

    await bakery.press(ADMIN, f"adm_toggle:{CROISSANT}")
    category = await bakery.press(CLIENT, "cat:2")
    assert any("Круассан сливочный" in button for button in category[-1].buttons())


async def test_stats_count_orders_without_cancelled(bakery):
    await place_order(bakery)
    await place_order(bakery)
    await bakery.press(ADMIN, "adm_status:1002:cancelled:0")

    stats = await bakery.press(ADMIN, "adm:stats")
    assert "Сегодня:</b> 1 заказ на 240 ₽" in stats[-1].text
    assert "отменено: 1" in stats[-1].text
    assert "Круассан сливочный — 2 шт" in stats[-1].text


async def test_admin_menu(bakery):
    await place_order(bakery)
    menu = await bakery.send(ADMIN, "/admin")
    assert menu[0].buttons()[0] == "📋 Активные заказы (1)"
