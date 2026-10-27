"""Корзина и кнопки +/- (и в карточке товара, и в самой корзине)."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database import cart as cart_db
from database import catalog as catalog_db
from handlers.catalog import render_card
from keyboards.callbacks import CartCB, QtyCB
from keyboards.cart import cart_kb
from keyboards.main_menu import BTN_CART
from utils.formatting import format_price
from utils.messages import show

router = Router(name="cart")


async def render_cart(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    items = await cart_db.get_cart(user_id)
    if not items:
        return "🛒 Корзина пуста.\n\nЗагляните в каталог — там много вкусного!", cart_kb(items)

    lines = ["🛒 <b>Ваша корзина</b>", ""]
    total = 0
    has_unavailable = False
    for number, item in enumerate(items, start=1):
        name = escape(item["name"])
        if item["is_available"]:
            total += item["subtotal"]
            lines.append(
                f"{number}. {name}\n"
                f"     {item['quantity']} × {format_price(item['price'])} = "
                f"<b>{format_price(item['subtotal'])}</b>"
            )
        else:
            # Товар закончился, пока лежал в корзине — в сумму не считаем
            has_unavailable = True
            lines.append(f"{number}. <s>{name}</s> — нет в наличии ❌")

    lines += ["", f"Итого: <b>{format_price(total)}</b>"]
    if has_unavailable:
        lines.append("\n⚠️ Товары, которых нет в наличии, не попадут в заказ.")
    return "\n".join(lines), cart_kb(items)


@router.message(F.text == BTN_CART)
async def open_cart_from_menu(message: Message) -> None:
    text, markup = await render_cart(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(CartCB.filter(F.action == "open"))
async def open_cart(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, lambda: render_cart(callback.from_user.id))


@router.callback_query(CartCB.filter(F.action == "clear"))
async def clear_cart(callback: CallbackQuery) -> None:
    await cart_db.clear_cart(callback.from_user.id)
    await callback.answer("Корзина очищена")
    await show(callback, lambda: render_cart(callback.from_user.id))


@router.callback_query(QtyCB.filter())
async def change_quantity(callback: CallbackQuery, callback_data: QtyCB) -> None:
    """Общий обработчик ➕/➖. После изменения перерисовывает тот экран, где нажали."""
    user_id = callback.from_user.id
    product = await catalog_db.get_product(callback_data.product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return

    adding = callback_data.delta > 0
    if adding and not product["is_available"]:
        await callback.answer("Этот товар закончился 😔", show_alert=True)
        return

    before = await cart_db.get_quantity(user_id, product["id"])
    if adding and before >= cart_db.MAX_QUANTITY:
        await callback.answer(f"Больше {cart_db.MAX_QUANTITY} шт добавить нельзя", show_alert=True)
        return

    quantity = await cart_db.change_quantity(user_id, product["id"], callback_data.delta)

    # Отвечаем ДО перерисовки: пока ответа нет, Telegram не даёт нажать кнопку снова
    await callback.answer("Убрано из корзины" if quantity == 0 else f"В корзине: {quantity} шт")

    if callback_data.source == "card":
        await show(callback, lambda: render_card(product, user_id))
    else:
        await show(callback, lambda: render_cart(user_id))
