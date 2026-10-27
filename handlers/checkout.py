"""Оформление заказа по шагам: согласие на обработку ПД (один раз) -> имя ->
телефон -> способ получения -> адрес (для доставки) -> комментарий ->
способ оплаты (если включена оплата картой или по СБП) -> сводка и подтверждение."""

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

import texts
from config import DELIVERY_PRICE, FREE_DELIVERY_FROM, MIN_ONLINE_PAYMENT
from database import cart as cart_db
from database import orders as orders_db
from database import privacy as privacy_db
from keyboards.callbacks import CartCB, CheckoutCB, ConsentCB
from keyboards.checkout import (
    BTN_CANCEL,
    BTN_DELIVERY,
    BTN_NO_COMMENT,
    BTN_PAY_ON_DELIVERY,
    BTN_PAY_ONLINE,
    BTN_PAY_SBP,
    BTN_PICKUP,
    address_kb,
    cancel_kb,
    comment_kb,
    confirm_kb,
    delivery_type_kb,
    name_kb,
    payment_kb,
    phone_kb,
)
from keyboards.main_menu import main_menu_kb
from keyboards.privacy import consent_kb
from services import payments, sbp
from services.notifications import notify_admins_new_order
from states.checkout import Checkout
from utils.formatting import format_price
from utils.messages import fixed, show
from utils.order_text import format_order_body
from utils.validators import normalize_phone

router = Router(name="checkout")

MAX_NAME_LEN = 50
MAX_ADDRESS_LEN = 200
MAX_COMMENT_LEN = 300


async def available_cart(user_id: int) -> tuple[list, list]:
    """Корзина, разделённая на (в наличии, закончились)."""
    items = await cart_db.get_cart(user_id)
    return [i for i in items if i["is_available"]], [i for i in items if not i["is_available"]]


# ---------- Отмена на любом шаге ----------
# Этот хендлер стоит первым: иначе текст «❌ Отменить» на шаге «Имя»
# сохранился бы как имя.

@router.message(StateFilter(Checkout), F.text == BTN_CANCEL)
@router.message(StateFilter(Checkout), Command("cancel"))
async def cancel_by_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Оформление отменено. Товары остались в корзине 🛒", reply_markup=main_menu_kb())


@router.callback_query(CheckoutCB.filter(F.action == "cancel"))
async def cancel_by_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await show(callback, fixed("❌ Оформление отменено. Товары остались в корзине."))
    await callback.bot.send_message(
        callback.from_user.id, "Возвращаемся в меню 👇", reply_markup=main_menu_kb()
    )


# ---------- Шаг 0: старт из корзины ----------

@router.callback_query(CartCB.filter(F.action == "checkout"))
async def start_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    available, _ = await available_cart(user_id)
    if not available:
        await callback.answer("В корзине нет товаров в наличии", show_alert=True)
        return
    if await privacy_db.get_pending_request(user_id):
        await callback.answer(texts.PRIVACY_BLOCKED_CHECKOUT, show_alert=True)
        return
    await callback.answer()
    await state.clear()

    # Согласие спрашиваем один раз (и заново — только если поменялась версия текста)
    if not await privacy_db.has_consent(user_id, texts.CONSENT_VERSION):
        await state.set_state(Checkout.consent)
        await callback.bot.send_message(user_id, texts.CONSENT_TEXT, reply_markup=consent_kb())
        return

    await ask_name(callback.bot, callback.from_user, state)


# ---------- Шаг 0.5: согласие на обработку персональных данных ----------

@router.callback_query(ConsentCB.filter(F.action == "agree"))
async def consent_agree(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if await privacy_db.get_pending_request(user_id):
        await callback.answer(texts.PRIVACY_BLOCKED_CHECKOUT, show_alert=True)
        return

    # Сразу переводим на следующий шаг: повторное нажатие «Согласен» уже не начнёт его второй раз
    was_waiting = await state.get_state() == Checkout.consent.state
    if was_waiting:
        await state.set_state(Checkout.name)

    await callback.answer("Спасибо! Согласие сохранено")
    await privacy_db.give_consent(user_id, texts.CONSENT_VERSION)
    # Оставляем текст согласия в чате, но убираем кнопки — как отметку «согласие дано»
    await show(callback, fixed(texts.CONSENT_TEXT + "\n\n✅ <b>Вы дали согласие</b>"))

    if not was_waiting:
        return  # нажали «Согласен» в старом сообщении — согласие сохранили, оформление не начинаем
    available, _ = await available_cart(user_id)
    if not available:
        await state.clear()
        await callback.bot.send_message(
            user_id, "Корзина пуста — добавьте товары, чтобы оформить заказ.", reply_markup=main_menu_kb()
        )
        return
    await ask_name(callback.bot, callback.from_user, state)


@router.callback_query(ConsentCB.filter(F.action == "decline"))
async def consent_decline(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if await state.get_state() == Checkout.consent.state:
        await state.clear()
    await show(callback, fixed(texts.CONSENT_DECLINED))


# ---------- Шаг 1: имя ----------

async def ask_name(bot: Bot, user: User, state: FSMContext) -> None:
    # Данные из прошлого заказа предложим кнопками, чтобы не вводить заново
    saved = await orders_db.get_last_contacts(user.id)
    await state.update_data(saved_phone=saved["phone"], saved_address=saved["address"])
    await state.set_state(Checkout.name)

    # Подсказки имени без повторов: из прошлого заказа и из профиля Telegram
    suggestions = []
    for name in (saved["customer_name"], user.first_name):
        if name and name not in suggestions and 2 <= len(name) <= MAX_NAME_LEN:
            suggestions.append(name)

    await bot.send_message(
        user.id,
        "📝 <b>Оформление заказа</b>\n\n"
        "Как к вам обращаться? Напишите имя или нажмите кнопку ниже.\n\n"
        "<i>Отменить оформление можно на любом шаге кнопкой «❌ Отменить».</i>",
        reply_markup=name_kb(suggestions),
    )


@router.message(Checkout.name, F.text)
async def step_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 2 <= len(name) <= MAX_NAME_LEN:
        await message.answer(f"Имя должно быть от 2 до {MAX_NAME_LEN} символов. Попробуйте ещё раз.")
        return
    await state.update_data(customer_name=name)

    data = await state.get_data()
    await state.set_state(Checkout.phone)
    await message.answer(
        "📞 Ваш номер телефона?\n\n"
        "Нажмите «📱 Поделиться контактом» или напишите номер, например <code>+7 900 123-45-67</code>.",
        reply_markup=phone_kb(data.get("saved_phone")),
    )


# ---------- Шаг 2: телефон ----------

@router.message(Checkout.phone, F.contact)
async def step_phone_contact(message: Message, state: FSMContext) -> None:
    await save_phone(message, state, message.contact.phone_number)


@router.message(Checkout.phone, F.text)
async def step_phone_text(message: Message, state: FSMContext) -> None:
    await save_phone(message, state, message.text)


async def save_phone(message: Message, state: FSMContext, raw_phone: str) -> None:
    phone = normalize_phone(raw_phone)
    if phone is None:
        await message.answer(
            "Не похоже на номер телефона 🤔\n"
            "Напишите его цифрами, например <code>+7 900 123-45-67</code>, "
            "или нажмите «📱 Поделиться контактом»."
        )
        return
    await state.update_data(phone=phone)

    # Подскажем, сколько будет стоить доставка именно для этой корзины
    available, _ = await available_cart(message.from_user.id)
    items_total = sum(i["subtotal"] for i in available)
    delivery_price = orders_db.calc_delivery_price(items_total, "delivery")
    delivery_note = (
        "бесплатно для вашего заказа"
        if delivery_price == 0
        else f"{format_price(DELIVERY_PRICE)}, бесплатно от {format_price(FREE_DELIVERY_FROM)}"
    )

    await state.set_state(Checkout.delivery_type)
    await message.answer(
        "🚚 Как хотите получить заказ?\n\n"
        f"🏃 <b>Самовывоз</b> — {texts.PICKUP_ADDRESS}, бесплатно\n"
        f"🚚 <b>Доставка</b> — {delivery_note}",
        reply_markup=delivery_type_kb(),
    )


# ---------- Шаг 3: самовывоз или доставка ----------

@router.message(Checkout.delivery_type, F.text == BTN_PICKUP)
async def step_pickup(message: Message, state: FSMContext) -> None:
    await state.update_data(delivery_type="pickup", address=None)
    await ask_comment(message, state)


@router.message(Checkout.delivery_type, F.text == BTN_DELIVERY)
async def step_delivery(message: Message, state: FSMContext) -> None:
    await state.update_data(delivery_type="delivery")
    data = await state.get_data()
    await state.set_state(Checkout.address)
    await message.answer(
        "📍 Напишите адрес доставки: улица, дом, квартира, подъезд и этаж.",
        reply_markup=address_kb(data.get("saved_address")),
    )


@router.message(Checkout.delivery_type)
async def step_delivery_type_wrong(message: Message) -> None:
    await message.answer("Выберите кнопкой ниже: «🏃 Самовывоз» или «🚚 Доставка» 👇")


# ---------- Шаг 4: адрес (только для доставки) ----------

@router.message(Checkout.address, F.text)
async def step_address(message: Message, state: FSMContext) -> None:
    address = message.text.strip()
    if not 5 <= len(address) <= MAX_ADDRESS_LEN:
        await message.answer(
            f"Адрес должен быть от 5 до {MAX_ADDRESS_LEN} символов. Напишите, пожалуйста, подробнее."
        )
        return
    await state.update_data(address=address)
    await ask_comment(message, state)


# ---------- Шаг 5: комментарий ----------

async def ask_comment(message: Message, state: FSMContext) -> None:
    await state.set_state(Checkout.comment)
    await message.answer(
        "💬 Комментарий к заказу?\n\n"
        "Например: «к 18:00», «позвонить за 10 минут». "
        "Если не нужен — нажмите «Без комментария».",
        reply_markup=comment_kb(),
    )


@router.message(Checkout.comment, F.text)
async def step_comment(message: Message, state: FSMContext) -> None:
    comment = message.text.strip()
    if comment == BTN_NO_COMMENT:
        comment = None
    elif len(comment) > MAX_COMMENT_LEN:
        await message.answer(f"Слишком длинный комментарий — максимум {MAX_COMMENT_LEN} символов.")
        return
    await state.update_data(comment=comment)
    await ask_payment(message, state)


# ---------- Шаг 6: способ оплаты ----------

async def online_methods(user_id: int, delivery_type: str) -> tuple[bool, bool]:
    """Какие способы онлайн-оплаты сейчас доступны: (картой в Telegram, по СБП).

    Картой — если задан PAYMENT_TOKEN и сумма не меньше минимума Telegram.
    СБП — если заданы ключи ЮKassa (минимум у СБП — 1 ₽).
    """
    available, _ = await available_cart(user_id)
    items_total = sum(i["subtotal"] for i in available)
    total = items_total + orders_db.calc_delivery_price(items_total, delivery_type)
    return payments.is_enabled() and total >= MIN_ONLINE_PAYMENT, sbp.is_enabled()


async def ask_payment(message: Message, state: FSMContext) -> None:
    """Спрашиваем, только если доступен хотя бы один способ онлайн-оплаты."""
    data = await state.get_data()
    card, sbp_on = await online_methods(message.from_user.id, data["delivery_type"])

    if not (card or sbp_on):
        await state.update_data(payment_method="cash")
        await show_summary(message, state)
        return

    lines = ["💳 Как будете оплачивать?", ""]
    if card:
        lines.append("💳 <b>Картой в Telegram</b> — форма оплаты откроется прямо в чате")
    if sbp_on:
        lines.append("⚡ <b>По СБП</b> — через приложение вашего банка")
    lines.append("💵 <b>При получении</b> — наличными или картой")

    await state.set_state(Checkout.payment)
    await message.answer("\n".join(lines), reply_markup=payment_kb(card, sbp_on))


# Текст кнопки -> способ оплаты в базе
PAYMENT_BUTTONS = {BTN_PAY_ONLINE: "online", BTN_PAY_SBP: "sbp", BTN_PAY_ON_DELIVERY: "cash"}


@router.message(Checkout.payment, F.text.in_(PAYMENT_BUTTONS))
async def step_payment(message: Message, state: FSMContext) -> None:
    method = PAYMENT_BUTTONS[message.text]
    data = await state.get_data()
    card, sbp_on = await online_methods(message.from_user.id, data["delivery_type"])
    # Кнопку способа, которого нет на клавиатуре, можно только напечатать вручную
    if (method == "online" and not card) or (method == "sbp" and not sbp_on):
        await message.answer("Этот способ оплаты сейчас недоступен — выберите кнопкой ниже 👇")
        return
    await state.update_data(payment_method=method)
    await show_summary(message, state)


@router.message(Checkout.payment)
async def step_payment_wrong(message: Message) -> None:
    await message.answer("Выберите способ оплаты кнопкой ниже 👇")


# ---------- Шаг 7: сводка и подтверждение ----------

async def show_summary(message: Message, state: FSMContext) -> None:
    available, unavailable = await available_cart(message.from_user.id)
    if not available:
        # Пока оформляли, корзину очистили (или всё закончилось)
        await state.clear()
        await message.answer(
            "😔 В корзине не осталось товаров в наличии — оформить нечего.",
            reply_markup=main_menu_kb(),
        )
        return

    data = await state.get_data()
    items_total = sum(i["subtotal"] for i in available)
    delivery_price = orders_db.calc_delivery_price(items_total, data["delivery_type"])
    draft = {**data, "items_total": items_total, "delivery_price": delivery_price,
             "total": items_total + delivery_price}

    text = "🧾 <b>Проверьте заказ</b>\n\n" + format_order_body(draft, available)
    if unavailable:
        names = ", ".join(escape(i["name"]) for i in unavailable)
        text += f"\n\n⚠️ Не вошли (нет в наличии): {names}"

    await state.set_state(Checkout.confirm)
    # Reply-кнопку и inline-кнопки нельзя прикрепить к одному сообщению — шлём два
    await message.answer("Почти готово! 👇", reply_markup=cancel_kb())
    await message.answer(text, reply_markup=confirm_kb(online=data["payment_method"] != "cash"))


@router.callback_query(CheckoutCB.filter(F.action == "confirm"))
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != Checkout.confirm.state:
        # Старая сводка: заказ уже оформлен или оформление отменено
        await callback.answer("Это оформление уже завершено или отменено", show_alert=True)
        return

    data = await state.get_data()
    # Сбрасываем шаги сразу — повторное нажатие уже не пройдёт проверку выше
    await state.clear()
    await callback.answer()

    user_id = callback.from_user.id
    order_id = await orders_db.create_order(user_id, data)
    if order_id is None:
        await show(callback, fixed("😔 Не удалось оформить заказ: в корзине нет товаров в наличии."))
        await callback.bot.send_message(user_id, "Возвращаемся в меню 👇", reply_markup=main_menu_kb())
        return

    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)

    if order["payment_method"] == "online":
        # Онлайн: менеджеру заказ уйдёт только после успешной оплаты (handlers/payments.py).
        # Сначала возвращаем главное меню (сейчас внизу кнопка «Отменить»), потом счёт —
        # так счёт будет последним сообщением в чате.
        await callback.bot.send_message(user_id, "Формирую счёт на оплату…", reply_markup=main_menu_kb())
        if not await payments.send_invoice(callback.bot, order_id):
            # Telegram не принял счёт (например, сумма меньше минимума) — отменяем, товары в корзину
            await orders_db.cancel_unpaid(order_id, user_id)
            await show(callback, fixed(f"❌ Заказ №{order_id} отменён: онлайн-оплата недоступна."))
            await callback.bot.send_message(user_id, texts.PAYMENT_UNAVAILABLE.format(id=order_id))
            return
        waiting_text = texts.PAYMENT_WAITING.format(id=order_id) + "\n\n" + format_order_body(order, items)
        await show(callback, fixed(waiting_text))
        return

    if order["payment_method"] == "sbp":
        # СБП: бот сам создаёт платёж в ЮKassa и следит за оплатой (services/sbp.py)
        await callback.bot.send_message(user_id, "Формирую ссылку на оплату…", reply_markup=main_menu_kb())
        if not await sbp.send_payment_link(callback.bot, order_id):
            await orders_db.cancel_unpaid(order_id, user_id)
            await show(callback, fixed(f"❌ Заказ №{order_id} отменён: оплата по СБП недоступна."))
            await callback.bot.send_message(user_id, texts.PAYMENT_UNAVAILABLE.format(id=order_id))
            return
        waiting_text = texts.SBP_WAITING.format(id=order_id) + "\n\n" + format_order_body(order, items)
        await show(callback, fixed(waiting_text))
        return

    text = (
        f"✅ <b>Заказ №{order_id} принят!</b>\n\n"
        f"{format_order_body(order, items)}\n\n"
        f"Статус: {orders_db.STATUS_LABELS[order['status']]}. "
        "Мы пришлём уведомление, когда он изменится."
    )
    # Убираем кнопки «Подтвердить/Отменить» из сводки и показываем итог
    await show(callback, fixed(text))
    await callback.bot.send_message(
        user_id,
        "Спасибо за заказ! 🥐 Следить за ним можно в разделе «📦 Мои заказы».",
        reply_markup=main_menu_kb(),
    )
    # Клиенту уже ответили — теперь сообщаем менеджерам
    await notify_admins_new_order(callback.bot, order_id)


# ---------- Всё остальное во время оформления ----------
# Стоит последним в роутере: сюда попадают стикеры, фото и т.п.

@router.message(StateFilter(Checkout))
async def wrong_input(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current == Checkout.consent.state:
        await message.answer("Нажмите «✅ Согласен» или «❌ Отказаться» под текстом согласия 👆")
    elif current == Checkout.confirm.state:
        await message.answer("Нажмите «✅ Подтвердить заказ» или «❌ Отменить» под сводкой заказа 👆")
    else:
        await message.answer("Пожалуйста, ответьте текстом или кнопкой ниже 👇")
