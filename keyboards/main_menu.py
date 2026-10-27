"""Главное меню — постоянные кнопки внизу экрана (reply-клавиатура)."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Тексты кнопок вынесены в константы: по ним же хендлеры узнают нажатие
BTN_CATALOG = "🥐 Каталог"
BTN_CART = "🛒 Корзина"
BTN_ORDERS = "📦 Мои заказы"
BTN_ABOUT = "ℹ️ О нас"


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_CART)],
            [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_ABOUT)],
        ],
        resize_keyboard=True,  # компактные кнопки вместо огромных
        input_field_placeholder="Выберите пункт меню",
    )
