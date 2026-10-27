"""Кнопки для персональных данных: согласие, /privacy, запросы у менеджера."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiosqlite import Row

from config import config
from keyboards.callbacks import POLICY, AdminCB, AdminPrivacyCB, ConsentCB, PrivacyCB
from utils.formatting import format_datetime


def _policy_button(builder: InlineKeyboardBuilder) -> None:
    """Кнопка «Политика»: ссылка, если она задана в .env, иначе бот пришлёт файл PRIVACY.md."""
    text = "📄 Политика обработки данных"
    if config.privacy_policy_url:
        builder.button(text=text, url=config.privacy_policy_url)
    else:
        builder.button(text=text, callback_data=POLICY)


def consent_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _policy_button(builder)
    builder.button(text="✅ Согласен", callback_data=ConsentCB(action="agree"))
    builder.button(text="❌ Отказаться", callback_data=ConsentCB(action="decline"))
    builder.adjust(1, 2)
    return builder.as_markup()


def privacy_kb(can_delete: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _policy_button(builder)
    if can_delete:
        builder.button(text="🗑 Отозвать согласие и удалить данные", callback_data=PrivacyCB(action="delete_ask"))
    builder.adjust(1)
    return builder.as_markup()


def delete_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить мои данные", callback_data=PrivacyCB(action="delete_confirm"))
    builder.button(text="⬅️ Нет, назад", callback_data=PrivacyCB(action="back"))
    builder.adjust(1)
    return builder.as_markup()


# ---------- Менеджер ----------

def manager_request_kb(request: Row) -> InlineKeyboardMarkup:
    """Карточка запроса на удаление: решить можно, только пока запрос ждёт менеджера."""
    builder = InlineKeyboardBuilder()
    rows = []
    if request["status"] == "waiting_manager":
        builder.button(text="✅ Обезличить данные", callback_data=AdminPrivacyCB(id=request["id"], action="approve"))
        builder.button(text="❌ Отклонить", callback_data=AdminPrivacyCB(id=request["id"], action="reject"))
        rows.append(2)
    builder.button(text="⬅️ К запросам", callback_data=AdminCB(section="privacy"))
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def admin_privacy_kb(waiting: list[Row]) -> InlineKeyboardMarkup:
    """Раздел /admin → «Персональные данные»: запросы, ждущие решения, и выгрузка журнала."""
    builder = InlineKeyboardBuilder()
    for request in waiting:
        date = format_datetime(request["requested_at"]).split()[0]  # только дата: «25.10.2026»
        builder.button(
            text=f"Запрос №{request['id']} · ID {request['user_id']} · {date}",
            callback_data=AdminPrivacyCB(id=request["id"], action="open"),
        )
    builder.button(text="📥 Журнал (CSV)", callback_data=AdminCB(section="privacy_log"))
    builder.button(text="⬅️ Меню", callback_data=AdminCB(section="menu"))
    builder.adjust(*[1] * len(waiting), 2)
    return builder.as_markup()
