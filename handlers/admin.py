"""Всё для менеджера: /admin, смена статусов заказов, наличие товаров, статистика,
запросы на удаление персональных данных и журнал.

Фильтр IsAdmin повешен на весь роутер: если ID пользователя нет в ADMIN_IDS,
ни один хендлер отсюда не сработает — для обычного клиента /admin выглядит
как неизвестная команда.
"""

import csv
import io
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from config import config
from database import catalog as catalog_db
from database import orders as orders_db
from database import privacy as privacy_db
from filters.admin import IsAdmin
from keyboards.admin import (
    active_orders_kb,
    admin_menu_kb,
    cancel_confirm_kb,
    stats_kb,
    stock_categories_kb,
    stock_products_kb,
)
from keyboards.callbacks import (
    AdminCB,
    AdminOrderCB,
    AdminPrivacyCB,
    AdminStatusCB,
    AdminStockCategoryCB,
    AdminToggleCB,
)
from keyboards.privacy import admin_privacy_kb
from services import privacy as privacy_service
from services.notifications import notify_client_status, refresh_admin_notifications, render_admin_order
from utils.formatting import format_price, plural
from utils.messages import show

router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ---------- Экраны панели: каждая функция возвращает (текст, кнопки) ----------

async def render_menu() -> tuple[str, InlineKeyboardMarkup]:
    active_count = await orders_db.count_active_orders()
    privacy_waiting = len(await privacy_db.get_requests("waiting_manager"))
    return "🛠 <b>Панель менеджера</b>\n\nВыберите раздел:", admin_menu_kb(active_count, privacy_waiting)


async def render_active_orders() -> tuple[str, InlineKeyboardMarkup]:
    orders = await orders_db.get_active_orders()
    if orders:
        text = (
            "📋 <b>Активные заказы</b>\n\n"
            f"В работе: {plural(len(orders), 'заказ', 'заказа', 'заказов')}. "
            "Сверху — самые ранние. 🚚 доставка, 🏃 самовывоз."
        )
    else:
        text = "📋 <b>Активные заказы</b>\n\nАктивных заказов нет 🎉"
    return text, active_orders_kb(orders)


async def render_stock_categories() -> tuple[str, InlineKeyboardMarkup]:
    categories = await catalog_db.get_categories()
    text = (
        "🥐 <b>Наличие товаров</b>\n\n"
        "Выберите категорию. Товары, которых нет в наличии, клиенты не видят в каталоге."
    )
    return text, stock_categories_kb(categories)


async def render_stock_category(category_id: int) -> tuple[str, InlineKeyboardMarkup]:
    category = await catalog_db.get_category(category_id)
    products = await catalog_db.get_products(category_id, only_available=False)
    text = (
        f"<b>{escape(category['name'])}</b>\n\n"
        "Нажмите на товар, чтобы переключить наличие:\n"
        "✅ — в наличии, ❌ — нет"
    )
    return text, stock_products_kb(products)


async def render_stats() -> tuple[str, InlineKeyboardMarkup]:
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    periods = [
        ("Сегодня", today),
        ("За 7 дней", today - timedelta(days=6)),
        ("За всё время", datetime(2000, 1, 1)),
    ]

    lines = ["📊 <b>Статистика</b>", "<i>Суммы — без отменённых заказов</i>", ""]
    for title, since in periods:
        stats = await orders_db.get_period_stats(since.strftime("%Y-%m-%d %H:%M:%S"))
        count, revenue = stats["orders"], stats["revenue"]
        line = f"<b>{title}:</b> {plural(count, 'заказ', 'заказа', 'заказов')} на {format_price(revenue)}"
        if count:
            line += f", средний чек {format_price(revenue // count)}"
        if stats["cancelled"]:
            line += f" (отменено: {stats['cancelled']})"
        lines.append(line)

    active = await orders_db.get_active_counts()
    lines += ["", "<b>Сейчас в работе:</b>"]
    lines += [f"{orders_db.STATUS_LABELS[status]} — {count}" for status, count in active.items()]

    top = await orders_db.get_top_products()
    if top:
        lines += ["", "🏆 <b>Топ товаров:</b>"]
        lines += [f"{n}. {escape(row['name'])} — {row['quantity']} шт" for n, row in enumerate(top, start=1)]

    # Время обновления: видно, что «🔄 Обновить» сработало
    lines += ["", f"<i>Обновлено в {now:%H:%M:%S}</i>"]
    return "\n".join(lines), stats_kb()


async def render_privacy_section() -> tuple[str, InlineKeyboardMarkup]:
    waiting_manager = await privacy_db.get_requests("waiting_manager")
    waiting_orders = await privacy_db.get_requests("waiting_orders")
    mode = (
        "автоматически, сразу после запроса"
        if config.privacy_deletion_mode == "auto"
        else "после решения менеджера"
    )
    text = (
        "🔐 <b>Персональные данные</b>\n\n"
        f"Удаление данных по запросу клиента: <b>{mode}</b>\n"
        f"<i>(меняется в .env: PRIVACY_DELETION_MODE=auto или manager)</i>\n\n"
        f"Ждут решения менеджера: {len(waiting_manager)}\n"
        f"Ждут завершения заказов: {len(waiting_orders)}\n\n"
        "Журнал — все события с данными клиентов: согласия, отзывы, удаления. "
        "Выгрузите его, если нужно подтвердить, что запрос выполнен."
    )
    return text, admin_privacy_kb(waiting_manager)


SECTIONS = {
    "menu": render_menu,
    "orders": render_active_orders,
    "stock": render_stock_categories,
    "stats": render_stats,
    "privacy": render_privacy_section,
}


# ---------- Хендлеры ----------

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    text, markup = await render_menu()
    await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.section == "privacy_log"))
async def export_privacy_log(callback: CallbackQuery) -> None:
    """Журнал событий с персональными данными — файлом CSV (открывается в Excel)."""
    await callback.answer("Готовлю файл…")
    buffer = io.StringIO()
    # Точка с запятой и BOM (utf-8-sig) — чтобы русский Excel открыл файл без «кракозябр»
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Дата и время", "Событие", "Telegram ID", "Кто", "Подробности"])
    for row in await privacy_db.get_log():
        writer.writerow([
            row["created_at"],
            privacy_db.EVENT_LABELS.get(row["event"], row["event"]),
            row["user_id"],
            privacy_service.actor_label(row["actor"]),
            row["details"],
        ])
    file = BufferedInputFile(
        buffer.getvalue().encode("utf-8-sig"),
        filename=f"privacy_log_{datetime.now():%Y-%m-%d}.csv",
    )
    await callback.bot.send_document(
        callback.from_user.id, file, caption="📥 Журнал событий с персональными данными"
    )


@router.callback_query(AdminCB.filter())
async def open_section(callback: CallbackQuery, callback_data: AdminCB) -> None:
    render = SECTIONS.get(callback_data.section)
    if render is None:
        await callback.answer()
        return
    await callback.answer()
    await show(callback, render)


@router.callback_query(AdminOrderCB.filter(F.action == "open"))
async def open_order(callback: CallbackQuery, callback_data: AdminOrderCB) -> None:
    if await orders_db.get_order(callback_data.id) is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: render_admin_order(callback_data.id, callback_data.from_list))


@router.callback_query(AdminOrderCB.filter(F.action == "cancel_ask"))
async def ask_cancel(callback: CallbackQuery, callback_data: AdminOrderCB) -> None:
    """Перед отменой переспрашиваем — случайно нажатую отмену не вернуть."""
    order = await orders_db.get_order(callback_data.id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer()

    async def render() -> tuple[str, InlineKeyboardMarkup]:
        text, markup = await render_admin_order(order["id"], callback_data.from_list)
        if order["status"] not in orders_db.TRANSITIONS:
            return text, markup  # заказ уже завершён — отменять нечего
        text += "\n\n❓ <b>Точно отменить заказ?</b> Клиент получит уведомление."
        payment = await orders_db.get_accepted_payment(order["id"])
        if payment:
            # Деньги вернуть через бота нельзя — только в личном кабинете ЮKassa
            text += (
                "\n\n⚠️ <b>Заказ оплачен онлайн.</b> После отмены верните деньги клиенту "
                f"в личном кабинете ЮKassa. Платёж: <code>{payment['provider_charge_id']}</code>"
            )
        return text, cancel_confirm_kb(order["id"], callback_data.from_list)

    await show(callback, render)


@router.callback_query(AdminStatusCB.filter())
async def change_status(callback: CallbackQuery, callback_data: AdminStatusCB) -> None:
    order_id = callback_data.id
    changed = await orders_db.set_status(order_id, callback_data.status)

    if not changed:
        # Кнопка устарела: статус уже поменял другой менеджер (или эта же кнопка в другом сообщении)
        order = await orders_db.get_order(order_id)
        if order is None:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        current = orders_db.STATUS_LABELS[order["status"]]
        await callback.answer(f"Статус уже изменён: {current}", show_alert=True)
        await show(callback, lambda: render_admin_order(order_id, callback_data.from_list))
        return

    await callback.answer(f"Статус: {orders_db.STATUS_LABELS[callback_data.status]}")
    await show(callback, lambda: render_admin_order(order_id, callback_data.from_list))

    await notify_client_status(callback.bot, order_id)
    # Обновляем уведомления о заказе у всех менеджеров (кроме сообщения, где нажали)
    message = callback.message
    skip = (message.chat.id, message.message_id) if isinstance(message, Message) else None
    await refresh_admin_notifications(callback.bot, order_id, skip=skip)

    # Клиент мог ждать удаления данных до завершения заказов — проверяем
    if callback_data.status in ("done", "cancelled"):
        order = await orders_db.get_order(order_id)
        await privacy_service.on_order_finished(callback.bot, order["user_id"])


@router.callback_query(AdminStockCategoryCB.filter())
async def open_stock_category(callback: CallbackQuery, callback_data: AdminStockCategoryCB) -> None:
    if await catalog_db.get_category(callback_data.id) is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: render_stock_category(callback_data.id))


@router.callback_query(AdminToggleCB.filter())
async def toggle_product(callback: CallbackQuery, callback_data: AdminToggleCB) -> None:
    product = await catalog_db.get_product(callback_data.product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    available = await catalog_db.toggle_availability(product["id"])
    await callback.answer(f"✅ {product['name']}: в наличии" if available else f"❌ {product['name']}: нет в наличии")
    await show(callback, lambda: render_stock_category(product["category_id"]))


# ---------- Запросы на удаление персональных данных ----------

@router.callback_query(AdminPrivacyCB.filter(F.action == "open"))
async def open_privacy_request(callback: CallbackQuery, callback_data: AdminPrivacyCB) -> None:
    if await privacy_db.get_request(callback_data.id) is None:
        await callback.answer("Запрос не найден", show_alert=True)
        return
    await callback.answer()
    await show(callback, lambda: privacy_service.render_manager_request(callback_data.id))


@router.callback_query(AdminPrivacyCB.filter(F.action == "approve"))
async def approve_privacy_request(callback: CallbackQuery, callback_data: AdminPrivacyCB) -> None:
    result = await privacy_service.approve(callback_data.id, callback.from_user.id)
    if result is None:
        # Кнопка устарела: запрос уже обработал другой менеджер
        await callback.answer("Этот запрос уже обработан", show_alert=True)
        await show(callback, lambda: privacy_service.render_manager_request(callback_data.id))
        return
    await callback.answer("✅ Данные клиента обезличены, клиент получит уведомление", show_alert=True)
    await show(callback, lambda: privacy_service.render_manager_request(callback_data.id))
    user_id, order_ids = result
    await privacy_service.notify_approved(callback.bot, user_id, order_ids)


@router.callback_query(AdminPrivacyCB.filter(F.action == "reject"))
async def reject_privacy_request(callback: CallbackQuery, callback_data: AdminPrivacyCB) -> None:
    user_id = await privacy_service.reject(callback_data.id, callback.from_user.id)
    if user_id is None:
        await callback.answer("Этот запрос уже обработан", show_alert=True)
        await show(callback, lambda: privacy_service.render_manager_request(callback_data.id))
        return
    await callback.answer("Запрос отклонён, клиент получит уведомление", show_alert=True)
    await show(callback, lambda: privacy_service.render_manager_request(callback_data.id))
    await privacy_service.notify_rejected(callback.bot, user_id)
