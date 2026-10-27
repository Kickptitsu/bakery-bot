"""Каталог: категории -> товары -> карточка товара."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiosqlite import Row

from database import cart as cart_db
from database import catalog as catalog_db
from keyboards.callbacks import CATALOG, CategoryCB, ProductCB
from keyboards.catalog import categories_kb, product_card_kb, products_kb
from keyboards.main_menu import BTN_CATALOG
from utils.formatting import format_price
from utils.messages import show

router = Router(name="catalog")


# ---------- «Отрисовка» экранов: каждая функция возвращает (текст, кнопки) ----------

async def render_categories() -> tuple[str, InlineKeyboardMarkup]:
    categories = await catalog_db.get_categories()
    return "🥐 <b>Каталог</b>\n\nВыберите категорию:", categories_kb(categories)


async def render_category(category: Row, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    products = await catalog_db.get_products(category["id"])
    in_cart = await cart_db.get_quantities(user_id)
    if products:
        text = f"<b>{escape(category['name'])}</b>\n\nВыберите товар:"
    else:
        text = f"<b>{escape(category['name'])}</b>\n\nСейчас здесь пусто — всё раскупили 🙈"
    return text, products_kb(products, in_cart)


async def render_card(product: Row, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    # Количество читаем из базы прямо перед отрисовкой — так оно всегда актуальное
    quantity = await cart_db.get_quantity(user_id, product["id"])
    lines = [
        f"<b>{escape(product['name'])}</b>",
        escape(product["description"]),
        "",
        f"Цена: <b>{format_price(product['price'])}</b>",
    ]
    if quantity:
        lines.append(f"В корзине: {quantity} шт — {format_price(product['price'] * quantity)}")
    return "\n".join(lines), product_card_kb(product, quantity)


# ---------- Хендлеры ----------
# Во всех хендлерах кнопок сначала callback.answer(), потом перерисовка:
# пока бот не ответил на нажатие, Telegram игнорирует повторные нажатия.

@router.message(F.text == BTN_CATALOG)
async def open_catalog(message: Message) -> None:
    text, markup = await render_categories()
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == CATALOG)
async def back_to_categories(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, render_categories)


@router.callback_query(CategoryCB.filter())
async def open_category(callback: CallbackQuery, callback_data: CategoryCB) -> None:
    category = await catalog_db.get_category(callback_data.id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: render_category(category, callback.from_user.id))


@router.callback_query(ProductCB.filter())
async def open_product(callback: CallbackQuery, callback_data: ProductCB) -> None:
    product = await catalog_db.get_product(callback_data.id)
    if product is None or not product["is_available"]:
        # Кнопка могла остаться в старом сообщении, а товар уже закончился
        await callback.answer("Этого товара сейчас нет в наличии 😔", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: render_card(product, callback.from_user.id))
