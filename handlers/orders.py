"""Раздел «Мои заказы»: список последних заказов и подробности по каждому."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database import orders as orders_db
from keyboards.callbacks import MY_ORDERS, OrderCB
from keyboards.main_menu import BTN_ORDERS
from keyboards.orders import order_details_kb, orders_list_kb
from utils.formatting import format_datetime
from utils.messages import show
from utils.order_text import format_order_body

router = Router(name="orders")

ORDERS_LIMIT = 10


async def render_orders_list(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    orders = await orders_db.get_user_orders(user_id, limit=ORDERS_LIMIT)
    if not orders:
        return "📦 У вас пока нет заказов.", orders_list_kb(orders)
    text = (
        "📦 <b>Ваши заказы</b>\n\n"
        f"Последние {len(orders)}. Нажмите на заказ, чтобы посмотреть подробности."
    )
    return text, orders_list_kb(orders)


async def render_order(order_id: int) -> tuple[str, InlineKeyboardMarkup]:
    order = await orders_db.get_order(order_id)
    items = await orders_db.get_order_items(order_id)
    text = (
        f"<b>Заказ №{order['id']}</b> от {format_datetime(order['created_at'])}\n"
        f"Статус: <b>{orders_db.STATUS_LABELS[order['status']]}</b>\n\n"
        f"{format_order_body(order, items)}"
    )
    return text, order_details_kb(order)


@router.message(F.text == BTN_ORDERS)
async def my_orders(message: Message) -> None:
    text, markup = await render_orders_list(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == MY_ORDERS)
async def back_to_orders(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, lambda: render_orders_list(callback.from_user.id))


@router.callback_query(OrderCB.filter())
async def open_order(callback: CallbackQuery, callback_data: OrderCB) -> None:
    order = await orders_db.get_order(callback_data.id)
    # Показываем только свои заказы, даже если кто-то подделает данные кнопки
    if order is None or order["user_id"] != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: render_order(order["id"]))
