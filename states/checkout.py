"""Шаги оформления заказа.

FSM (машина состояний) — способ «помнить», на каком шаге диалога пользователь.
Бот ставит состояние, например Checkout.phone, и следующее сообщение
пользователя попадает в хендлер именно этого шага.
"""

from aiogram.fsm.state import State, StatesGroup


class Checkout(StatesGroup):
    consent = State()        # согласие на обработку персональных данных
    name = State()           # имя
    phone = State()          # телефон
    delivery_type = State()  # самовывоз или доставка
    address = State()        # адрес (только для доставки)
    comment = State()        # комментарий
    payment = State()        # оплата онлайн или при получении (если онлайн-оплата включена)
    confirm = State()        # итоговая сводка и подтверждение
