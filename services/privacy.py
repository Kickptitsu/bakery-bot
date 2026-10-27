"""Отзыв согласия и удаление (обезличивание) персональных данных.

Порядок обработки запроса:
1. Есть незавершённые заказы -> ждём (данные нужны, чтобы их выполнить).
   Когда последний заказ выполнен или отменён, запрос обрабатывается дальше.
2. PRIVACY_DELETION_MODE=auto (или заказов нет вовсе) -> обезличиваем сразу.
3. PRIVACY_DELETION_MODE=manager -> отправляем запрос менеджерам, решают они.
"""

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

import texts
from config import config
from database import privacy as privacy_db
from keyboards.privacy import manager_request_kb
from services.notifications import refresh_admin_notifications
from utils.formatting import format_datetime

logger = logging.getLogger(__name__)

REQUEST_STATUS_LABELS = {
    "new": "🆕 Создан",
    "waiting_orders": "⏳ Ждёт завершения заказов",
    "waiting_manager": "👀 Ждёт решения менеджера",
    "done": "✅ Выполнен — данные обезличены",
    "rejected": "❌ Отклонён менеджером",
}


def format_user_data(data: dict) -> list[str]:
    """Строки «что хранится» — для /privacy и для карточки запроса у менеджера."""
    lines = []
    if data["order_ids"]:
        line = f"• Заказы: {len(data['order_ids'])}"
        if data["active_order_ids"]:
            line += f" (незавершённых: {len(data['active_order_ids'])})"
        lines.append(line)
    if data["names"]:
        lines.append("• Имена: " + ", ".join(escape(name) for name in data["names"]))
    if data["phones"]:
        lines.append("• Телефоны: " + ", ".join(escape(phone) for phone in data["phones"]))
    if data["addresses"]:
        lines.append("• Адреса доставки: " + "; ".join(escape(addr) for addr in data["addresses"]))
    if data["comments"]:
        lines.append(f"• Комментарии к заказам: {data['comments']}")
    if data["cart_items"]:
        lines.append(f"• Товаров в корзине: {data['cart_items']}")
    return lines


def actor_label(actor: str) -> str:
    """'manager:42' -> 'менеджер 42' и т.п. — для людей."""
    if actor.startswith("manager:"):
        return f"менеджер {actor.split(':', 1)[1]}"
    return {"user": "пользователь", "auto": "бот (автоматически)"}.get(actor, actor)


async def _send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramAPIError as error:
        logger.warning("Не удалось отправить сообщение %s: %s", chat_id, error)


# ---------- Обработка запроса ----------

async def process_request(bot: Bot, request_id: int, user_id: int) -> str:
    """Двигает запрос дальше и возвращает текст для пользователя."""
    data = await privacy_db.get_user_data(user_id)

    if data["active_order_ids"]:
        if await privacy_db.set_request_status(request_id, "waiting_orders", ("new",)):
            await privacy_db.log_event(
                user_id, "deletion_postponed", "auto",
                f"запрос №{request_id}: незавершённых заказов — {len(data['active_order_ids'])}",
            )
        orders = ", ".join(f"№{order_id}" for order_id in data["active_order_ids"])
        return texts.PRIVACY_POSTPONED.format(orders=orders)

    # Заказов нет — менеджеру решать нечего, удаляем сразу в любом режиме
    if config.privacy_deletion_mode == "auto" or not data["order_ids"]:
        order_ids = await privacy_db.anonymize_user(user_id, request_id, actor="auto")
        if order_ids is not None:
            await _refresh_orders(bot, order_ids)
        return texts.PRIVACY_DONE if data["order_ids"] else texts.PRIVACY_DONE_NO_ORDERS

    if await privacy_db.set_request_status(request_id, "waiting_manager", ("new", "waiting_orders")):
        await privacy_db.log_event(user_id, "sent_to_manager", "auto", f"запрос №{request_id}")
        await notify_managers(bot, request_id)
    return texts.PRIVACY_SENT_TO_MANAGER


async def on_order_finished(bot: Bot, user_id: int) -> None:
    """Вызывается, когда заказ выполнен или отменён.

    Если пользователь ждал удаления данных и это был последний незавершённый
    заказ — продолжаем обработку запроса и сообщаем пользователю результат.
    """
    if user_id == privacy_db.ANONYMOUS_USER_ID:
        return
    request = await privacy_db.get_pending_request(user_id)
    if request is None or request["status"] != "waiting_orders":
        return
    data = await privacy_db.get_user_data(user_id)
    if data["active_order_ids"]:
        return
    text = await process_request(bot, request["id"], user_id)
    await _send(bot, user_id, text)


async def _refresh_orders(bot: Bot, order_ids: list[int]) -> None:
    """В уведомлениях менеджерам о заказах тоже заменяем данные на «[удалено]»."""
    for order_id in order_ids:
        await refresh_admin_notifications(bot, order_id)


# ---------- Менеджер ----------

async def render_manager_request(request_id: int) -> tuple[str, InlineKeyboardMarkup]:
    request = await privacy_db.get_request(request_id)
    user_id = request["user_id"]
    lines = [
        f"🔐 <b>Запрос на удаление данных №{request['id']}</b> от {format_datetime(request['requested_at'])}",
        f"Статус: <b>{REQUEST_STATUS_LABELS[request['status']]}</b>",
        f'Клиент: Telegram ID <code>{user_id}</code> (<a href="tg://user?id={user_id}">написать</a>)',
    ]
    if request["status"] in privacy_db.PENDING_STATUSES:
        data = await privacy_db.get_user_data(user_id)
        lines += ["", "<b>Будет обезличено:</b>", *format_user_data(data)]
        lines += ["", "Суммы и состав заказов останутся для статистики. Согласие клиент уже отозвал."]
    if request["processed_at"]:
        lines += ["", f"Обработан: {format_datetime(request['processed_at'])}, {actor_label(request['processed_by'])}"]
    return "\n".join(lines), manager_request_kb(request)


async def notify_managers(bot: Bot, request_id: int) -> None:
    text, markup = await render_manager_request(request_id)
    for admin_id in config.admin_ids:
        await _send(bot, admin_id, text, markup)


# Решение менеджера делится на две части: быстрая запись в базу (approve/reject),
# после неё хендлер сразу отвечает на нажатие, и только потом — рассылка
# уведомлений (notify_approved/notify_rejected), которая может занять время.

async def approve(request_id: int, admin_id: int) -> tuple[int, list[int]] | None:
    """Менеджер разрешил удаление: обезличивает данные.

    Возвращает (ID клиента, номера обезличенных заказов) или None,
    если запрос уже обработан кем-то другим.
    """
    request = await privacy_db.get_request(request_id)
    if request is None or request["status"] != "waiting_manager":
        return None
    order_ids = await privacy_db.anonymize_user(request["user_id"], request_id, actor=f"manager:{admin_id}")
    if order_ids is None:
        return None
    return request["user_id"], order_ids


async def notify_approved(bot: Bot, user_id: int, order_ids: list[int]) -> None:
    await _refresh_orders(bot, order_ids)
    await _send(bot, user_id, texts.PRIVACY_DONE)


async def reject(request_id: int, admin_id: int) -> int | None:
    """Менеджер отклонил запрос. Возвращает ID клиента или None, если запрос уже обработан."""
    request = await privacy_db.get_request(request_id)
    if request is None:
        return None
    actor = f"manager:{admin_id}"
    if not await privacy_db.set_request_status(request_id, "rejected", ("waiting_manager",), actor=actor):
        return None
    await privacy_db.log_event(request["user_id"], "rejected", actor, f"запрос №{request_id}")
    return request["user_id"]


async def notify_rejected(bot: Bot, user_id: int) -> None:
    await _send(bot, user_id, texts.PRIVACY_REJECTED)
