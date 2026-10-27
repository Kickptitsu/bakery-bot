"""Персональные данные: согласие, /privacy, обезличивание, режим «решает менеджер», журнал."""

import asyncio

import texts as bot_texts
from conftest import ADMIN, ADMIN2, CLIENT, texts
from database import cart as cart_db

CROISSANT = 5


async def order_and_confirm(bakery, consent: bool = True) -> None:
    await cart_db.change_quantity(CLIENT, CROISSANT, 2)
    await bakery.checkout(CLIENT, consent=consent, delivery=True, comment="позвонить")
    await bakery.press(CLIENT, "checkout:confirm")


async def finish_order(bakery, order_id: int) -> list:
    for status in ("cooking", "ready", "done"):
        calls = await bakery.press(ADMIN, f"adm_status:{order_id}:{status}:0")
    return calls


async def test_consent_is_asked_once_and_saved_with_version(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    first = await bakery.press(CLIENT, "cart:checkout")
    assert "Согласие на обработку персональных данных" in first[-1].text
    assert first[-1].buttons() == ["📄 Политика обработки данных", "✅ Согласен", "❌ Отказаться"]

    # Двойное «Согласен» — одна запись и один переход к имени
    await asyncio.gather(bakery.press(CLIENT, "consent:agree"), bakery.press(CLIENT, "consent:agree"))
    assert bakery.db("SELECT user_id, version, revoked_at FROM consents") == [
        (CLIENT, bot_texts.CONSENT_VERSION, None)
    ]
    name_prompts = [call for call in bakery.telegram.calls if "Как к вам обращаться" in call.text]
    assert len(name_prompts) == 1

    await bakery.send(CLIENT, "❌ Отменить")
    again = await bakery.press(CLIENT, "cart:checkout")
    assert "Как к вам обращаться" in again[-1].text   # второй раз не спрашиваем


async def test_decline_blocks_checkout_but_catalog_works(bakery):
    await cart_db.change_quantity(CLIENT, CROISSANT, 1)
    await bakery.press(CLIENT, "cart:checkout")
    calls = await bakery.press(CLIENT, "consent:decline")

    assert "Без согласия мы не сможем оформить заказ" in calls[-1].text
    assert bakery.db("SELECT * FROM consents") == []
    catalog = await bakery.send(CLIENT, "🥐 Каталог")
    assert "Выберите категорию" in catalog[0].text


async def test_policy_file_is_sent_without_customer_memo(bakery):
    calls = await bakery.press(CLIENT, "policy")
    document = calls[-1].method.document.data.decode("utf-8")
    assert document.startswith("# Политика обработки персональных данных")
    assert "ПАМЯТКА" not in document


async def test_privacy_shows_stored_data(bakery):
    await order_and_confirm(bakery)
    calls = await bakery.send(CLIENT, "/privacy")
    text = calls[0].text
    assert "✅ дано" in text and "+79001112233" in text and "ул. Мира, 1, кв. 5" in text
    assert "🗑 Отозвать согласие и удалить данные" in calls[0].buttons()


async def test_deletion_waits_for_active_order_then_anonymizes(bakery):
    """Режим auto: есть незавершённый заказ — ждём, после «Выполнен» обезличиваем сами."""
    await order_and_confirm(bakery)

    calls = await bakery.press(CLIENT, "privacy:delete_confirm")
    assert "незавершённые заказы: №1001" in calls[-1].text
    assert bakery.db("SELECT revoked_at IS NOT NULL FROM consents") == [(1,)]   # согласие отозвано сразу
    blocked = await bakery.press(CLIENT, "cart:checkout")
    assert blocked[0].alert  # новый заказ во время удаления не оформить

    calls = await finish_order(bakery, 1001)

    assert bakery.db("SELECT user_id, customer_name, phone, address, comment, total FROM orders") == [
        (0, "[удалено]", "[удалено]", "[удалено]", None, 440)
    ]
    assert any("ваши данные обезличены" in text for text in texts(calls, "send", CLIENT))
    # В уведомлениях менеджерам данные тоже заменены
    assert any("Данные клиента обезличены" in call.text for call in calls if call.kind == "edit")


async def test_manager_mode_approve_and_stale_second_admin(bakery, settings):
    settings(privacy_deletion_mode="manager")
    await order_and_confirm(bakery)
    await finish_order(bakery, 1001)

    calls = await bakery.press(CLIENT, "privacy:delete_confirm")
    assert "передан менеджеру" in calls[-1].text
    request_cards = [call for call in calls if call.kind == "send" and "Запрос на удаление" in call.text]
    assert {card.chat_id for card in request_cards} == {ADMIN, ADMIN2}
    assert bakery.db("SELECT customer_name FROM orders") == [("Анна",)]   # пока не удалено

    approve = await bakery.press(ADMIN, "adm_privacy:1:approve")
    assert approve[0].alert and "обезличены" in approve[0].text
    assert bakery.db("SELECT customer_name FROM orders") == [("[удалено]",)]

    stale = await bakery.press(ADMIN2, "adm_privacy:1:approve")
    assert "уже обработан" in stale[0].text


async def test_manager_can_reject(bakery, settings):
    settings(privacy_deletion_mode="manager")
    await order_and_confirm(bakery)
    await finish_order(bakery, 1001)
    await bakery.press(CLIENT, "privacy:delete_confirm")

    calls = await bakery.press(ADMIN, "adm_privacy:1:reject")
    assert any("не смог выполнить" in text for text in texts(calls, "send", CLIENT))
    assert bakery.db("SELECT customer_name FROM orders") == [("Анна",)]


async def test_log_does_not_link_person_to_orders(bakery):
    """В журнале нет номеров заказов — иначе по нему можно отменить обезличивание."""
    await order_and_confirm(bakery)
    await finish_order(bakery, 1001)
    await bakery.press(CLIENT, "privacy:delete_confirm")

    calls = await bakery.press(ADMIN, "adm:privacy_log")
    csv_text = calls[-1].method.document.data.decode("utf-8-sig")
    assert "Данные обезличены" in csv_text and "Согласие отозвано" in csv_text
    assert "1001" not in csv_text
