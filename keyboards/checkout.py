"""Кнопки шагов оформления заказа.

На шагах ввода используем reply-клавиатуру (кнопки вместо обычной клавиатуры):
нажатие такой кнопки просто отправляет её текст, как будто его напечатали.
"""

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.callbacks import CheckoutCB

BTN_CANCEL = "❌ Отменить"
BTN_SHARE_CONTACT = "📱 Поделиться контактом"
BTN_PICKUP = "🏃 Самовывоз"
BTN_DELIVERY = "🚚 Доставка"
BTN_NO_COMMENT = "Без комментария"
BTN_PAY_ONLINE = "💳 Картой в Telegram"
BTN_PAY_SBP = "⚡ По СБП"
BTN_PAY_ON_DELIVERY = "💵 При получении"


def _reply_kb(*rows: list[KeyboardButton], placeholder: str | None = None) -> ReplyKeyboardMarkup:
    """Собирает reply-клавиатуру и всегда добавляет внизу кнопку «Отменить»."""
    return ReplyKeyboardMarkup(
        keyboard=[*rows, [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def name_kb(suggestions: list[str]) -> ReplyKeyboardMarkup:
    """Подсказки имени: из прошлого заказа и из профиля Telegram."""
    rows = [[KeyboardButton(text=name) for name in suggestions]] if suggestions else []
    return _reply_kb(*rows, placeholder="Ваше имя")


def phone_kb(saved_phone: str | None) -> ReplyKeyboardMarkup:
    # request_contact=True: Telegram сам спросит разрешение и пришлёт номер
    rows = [[KeyboardButton(text=BTN_SHARE_CONTACT, request_contact=True)]]
    if saved_phone:
        rows.append([KeyboardButton(text=saved_phone)])
    return _reply_kb(*rows, placeholder="+7 900 123-45-67")


def delivery_type_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([KeyboardButton(text=BTN_PICKUP), KeyboardButton(text=BTN_DELIVERY)])


def address_kb(saved_address: str | None) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=saved_address)]] if saved_address else []
    return _reply_kb(*rows, placeholder="Улица, дом, квартира")


def comment_kb() -> ReplyKeyboardMarkup:
    return _reply_kb([KeyboardButton(text=BTN_NO_COMMENT)], placeholder="Комментарий к заказу")


def payment_kb(card: bool, sbp: bool) -> ReplyKeyboardMarkup:
    """Показываем только включённые способы онлайн-оплаты (см. ask_payment в handlers/checkout.py)."""
    online = []
    if card:
        online.append(KeyboardButton(text=BTN_PAY_ONLINE))
    if sbp:
        online.append(KeyboardButton(text=BTN_PAY_SBP))
    return _reply_kb(online, [KeyboardButton(text=BTN_PAY_ON_DELIVERY)])


def cancel_kb() -> ReplyKeyboardMarkup:
    return _reply_kb()


def confirm_kb(online: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    text = "✅ Подтвердить и перейти к оплате" if online else "✅ Подтвердить заказ"
    builder.button(text=text, callback_data=CheckoutCB(action="confirm"))
    builder.button(text="❌ Отменить", callback_data=CheckoutCB(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()
