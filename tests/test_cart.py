"""Каталог и корзина: кнопки ➕/➖, атомарный счётчик, быстрые нажатия."""

import asyncio

from conftest import CLIENT, texts
from database import cart as cart_db

CROISSANT = 5  # «Круассан сливочный» из демо-каталога, 120 ₽


async def test_plus_answers_first_then_redraws_card(bakery):
    """Путь кнопки ➕: ответ на нажатие приходит ДО перерисовки (иначе Telegram глотает нажатия)."""
    await bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")
    calls = await bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")

    assert [call.kind for call in calls] == ["answer", "edit"]
    assert calls[0].text == "В корзине: 2 шт"
    assert "В корзине: 2 шт — 240 ₽" in calls[1].text
    assert calls[1].buttons()[:3] == ["➖", "2 шт", "➕"]
    assert bakery.db("SELECT quantity FROM cart_items WHERE user_id = ?", CLIENT) == [(2,)]


async def test_minus_to_zero_removes_item(bakery):
    await bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")
    calls = await bakery.press(CLIENT, f"qty:{CROISSANT}:-1:card")

    assert texts(calls, "answer") == ["Убрано из корзины"]
    assert bakery.db("SELECT * FROM cart_items") == []


async def test_concurrent_changes_are_not_lost(bakery):
    """20 одновременных +1 дают ровно 20: количество меняется прямо в SQL."""
    await asyncio.gather(*[cart_db.change_quantity(CLIENT, CROISSANT, 1) for _ in range(20)])
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 20


async def test_quantity_is_capped(bakery):
    for _ in range(cart_db.MAX_QUANTITY + 10):
        await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == cart_db.MAX_QUANTITY

    calls = await bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")
    assert calls[0].alert and "Больше 50 шт" in calls[0].text


async def test_rapid_presses_show_latest_quantity(bakery):
    """10 быстрых ➕ при медленной сети: лишние перерисовки пропускаются, на экране — последнее значение."""
    bakery.telegram.edit_delay = 0.05
    presses = []
    for _ in range(10):
        presses.append(asyncio.create_task(bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")))
        await asyncio.sleep(0.01)
    await asyncio.gather(*presses)

    edits = [call for call in bakery.telegram.calls if call.kind == "edit"]
    assert len(edits) < 10
    assert "В корзине: 10 шт" in edits[-1].text
    assert await cart_db.get_quantity(CLIENT, CROISSANT) == 10


async def test_sold_out_product_cannot_be_added(bakery):
    bakery.db("UPDATE products SET is_available = 0 WHERE id = ?", CROISSANT)
    calls = await bakery.press(CLIENT, f"qty:{CROISSANT}:1:card")
    assert calls[0].alert and "закончился" in calls[0].text
    assert bakery.db("SELECT * FROM cart_items") == []


async def test_cart_shows_total_and_skips_unavailable(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)  # 240 ₽
    await cart_db.change_quantity(CLIENT, 6, 1)          # Круассан с миндалём, закончится
    bakery.db("UPDATE products SET is_available = 0 WHERE id = 6")

    calls = await bakery.send(CLIENT, "🛒 Корзина")
    assert "Итого: <b>240 ₽</b>" in calls[0].text
    assert "нет в наличии" in calls[0].text
