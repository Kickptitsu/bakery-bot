"""/privacy: какие данные о пользователе хранятся, отзыв согласия и удаление данных."""

import os
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from config import config
from database import privacy as privacy_db
from keyboards.callbacks import POLICY, PrivacyCB
from keyboards.privacy import delete_confirm_kb, privacy_kb
from services import privacy as privacy_service
from utils.formatting import format_datetime
from utils.messages import fixed, show

router = Router(name="privacy")

POLICY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "PRIVACY.md")


async def render_privacy(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    consent = await privacy_db.get_last_consent(user_id)
    data = await privacy_db.get_user_data(user_id)
    request = await privacy_db.get_pending_request(user_id)

    lines = ["🔐 <b>Ваши данные</b>", "", f"Telegram ID: <code>{user_id}</code>"]
    if consent is None:
        lines.append("Согласие на обработку данных: не давали")
    elif consent["revoked_at"]:
        lines.append(f"Согласие на обработку данных: ⛔ отозвано {format_datetime(consent['revoked_at'])}")
    else:
        lines.append(
            f"Согласие на обработку данных: ✅ дано {format_datetime(consent['accepted_at'])} "
            f"(версия текста {consent['version']})"
        )

    data_lines = privacy_service.format_user_data(data)
    lines.append("")
    if data_lines:
        lines += ["<b>Что мы храним:</b>", *data_lines]
    else:
        lines.append("Других персональных данных о вас мы не храним.")
    if consent is not None:
        # Честно говорим и про запись о согласии: её храним, чтобы подтвердить соблюдение закона
        lines.append(
            "\n<i>Также хранится запись о том, когда вы давали или отзывали согласие "
            "и на какую версию текста, — она нужна, чтобы подтвердить соблюдение закона.</i>"
        )

    if request:
        label = privacy_service.REQUEST_STATUS_LABELS[request["status"]]
        lines += [
            "",
            f"⏳ Запрос на удаление №{request['id']} от {format_datetime(request['requested_at'])}: {label}",
        ]

    has_active_consent = consent is not None and consent["revoked_at"] is None
    can_delete = request is None and (bool(data_lines) or has_active_consent)
    return "\n".join(lines), privacy_kb(can_delete)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    text, markup = await render_privacy(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == POLICY)
async def send_policy(callback: CallbackQuery) -> None:
    """Ссылка на политику не задана в .env — присылаем сам файл PRIVACY.md.

    Памятку для заказчика (она в HTML-комментарии <!-- ... -->) клиенту не показываем.
    """
    await callback.answer()
    with open(POLICY_FILE, encoding="utf-8") as file:
        policy = re.sub(r"<!--.*?-->", "", file.read(), flags=re.DOTALL).strip()
    await callback.bot.send_document(
        callback.from_user.id,
        BufferedInputFile(policy.encode("utf-8"), filename="Политика обработки персональных данных.md"),
        caption="📄 Политика обработки персональных данных",
    )


@router.callback_query(PrivacyCB.filter(F.action == "back"))
async def back(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, lambda: render_privacy(callback.from_user.id))


@router.callback_query(PrivacyCB.filter(F.action == "delete_ask"))
async def delete_ask(callback: CallbackQuery) -> None:
    await callback.answer()
    data = await privacy_db.get_user_data(callback.from_user.id)

    text = (
        "🗑 <b>Отозвать согласие и удалить данные?</b>\n\n"
        "• Согласие будет отозвано сразу.\n"
        "• Имя, телефон, адрес и комментарии в ваших заказах будут обезличены, корзина очищена.\n"
        "• Суммы и состав заказов останутся в статистике пекарни без привязки к вам.\n"
        "• Чтобы снова оформить заказ, нужно будет заново дать согласие."
    )
    if data["active_order_ids"]:
        orders = ", ".join(f"№{order_id}" for order_id in data["active_order_ids"])
        text += (
            f"\n\n⏳ У вас есть незавершённые заказы: {orders}. Данные удалим, "
            "когда они будут выполнены или отменены."
        )
    elif config.privacy_deletion_mode == "manager" and data["order_ids"]:
        text += "\n\nЗапрос рассмотрит менеджер."
    if data["unpaid_order_ids"]:
        orders = ", ".join(f"№{order_id}" for order_id in data["unpaid_order_ids"])
        text += f"\n\n💳 Неоплаченные заказы ({orders}) будут отменены."
    await show(callback, fixed(text, delete_confirm_kb()))


@router.callback_query(PrivacyCB.filter(F.action == "delete_confirm"))
async def delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    request_id = await privacy_db.create_deletion_request(user_id)
    if request_id is None:
        # Двойное нажатие: экран не перерисовываем, чтобы не затереть ответ на первое
        await callback.answer("Запрос на удаление уже создан — подробности в /privacy", show_alert=True)
        return
    await callback.answer()
    # Если пользователь был посреди оформления — имя и телефон из памяти бота тоже стираем
    await state.clear()
    text = await privacy_service.process_request(callback.bot, request_id, user_id)
    await show(callback, fixed(text))
