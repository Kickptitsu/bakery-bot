"""Оформление заказа по шагам (FSM): проверки ввода, отмена, защита от двойного подтверждения."""

import asyncio

from conftest import ADMIN, ADMIN2, CLIENT, admin_notification, texts
from database import cart as cart_db

CROISSANT = 5  # 120 ₽


async def test_full_checkout_with_delivery(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    summary = await bakery.checkout(CLIENT, delivery=True, comment="позвонить заранее")
    assert "Проверьте заказ" in summary[-1].text
    assert "Доставка: 200 ₽" in summary[-1].text and "Итого: 440 ₽" in summary[-1].text

    calls = await bakery.press(CLIENT, "checkout:confirm")

    assert bakery.db("SELECT id, status, phone, address, total FROM orders") == [
        (1001, "new", "+79001112233", "ул. Мира, 1, кв. 5", 440)
    ]
    assert bakery.db("SELECT * FROM cart_items") == []
    assert any("Заказ №1001 принят" in text for text in texts(calls, "edit"))
    # Оба менеджера получили карточку с кнопками статусов
    for admin_id in (ADMIN, ADMIN2):
        assert admin_notification(calls, admin_id).buttons() == ["👩‍🍳 Взять в работу", "❌ Отменить заказ"]


async def test_wrong_input_is_asked_again(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    await bakery.press(CLIENT, "cart:checkout")
    await bakery.press(CLIENT, "consent:agree")

    assert "от 2 до 50 символов" in (await bakery.send(CLIENT, "А"))[0].text
    await bakery.send(CLIENT, "Анна")
    assert "Не похоже на номер" in (await bakery.send(CLIENT, "позвоните мне"))[0].text
    # Кнопка «Поделиться контактом» присылает номер без «+» — бот его приводит к единому виду
    await bakery.share_contact(CLIENT, "79005554433")
    assert "Самовывоз" in (await bakery.send(CLIENT, "пешком"))[0].text


async def test_cancel_keeps_cart(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 3)
    await bakery.press(CLIENT, "cart:checkout")
    await bakery.press(CLIENT, "consent:agree")
    calls = await bakery.send(CLIENT, "❌ Отменить")

    assert "Товары остались в корзине" in calls[0].text
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 3
    # После отмены текст уже не считается именем
    assert "Не понял" in (await bakery.send(CLIENT, "Анна"))[0].text


async def test_double_confirm_creates_one_order(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    await bakery.checkout(CLIENT)
    await asyncio.gather(bakery.press(CLIENT, "checkout:confirm"), bakery.press(CLIENT, "checkout:confirm"))

    assert bakery.db("SELECT COUNT(*) FROM orders") == [(1,)]
    late = await bakery.press(CLIENT, "checkout:confirm")
    assert late[0].alert and "уже завершено" in late[0].text


async def test_start_resets_checkout(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    await bakery.press(CLIENT, "cart:checkout")
    await bakery.press(CLIENT, "consent:agree")
    await bakery.send(CLIENT, "/start")
    assert "Не понял" in (await bakery.send(CLIENT, "Вася"))[0].text


async def test_second_order_suggests_saved_contacts(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    await bakery.checkout(CLIENT, delivery=True)
    await bakery.press(CLIENT, "checkout:confirm")

    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    name_step = await bakery.press(CLIENT, "cart:checkout")   # согласие уже дано — сразу имя
    assert "Анна" in name_step[-1].buttons()
    phone_step = await bakery.send(CLIENT, "Анна")
    assert "+79001112233" in phone_step[0].buttons()


async def test_empty_cart_cannot_be_checked_out(bakery):
    calls = await bakery.press(CLIENT, "cart:checkout")
    assert calls[0].alert and "нет товаров" in calls[0].text
