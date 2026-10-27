"""Общая подготовка для всех тестов (pytest читает этот файл первым).

Тесты не трогают ваш .env и bakery.db и не ходят в интернет:
- настройки подменяются выдуманными ДО импорта кода бота;
- у каждого теста своя временная база;
- Telegram заменён FakeTelegram — он запоминает, что бот «отправил»;
- API ЮKassa заменяется FakeYooKassa (фикстура yookassa).

Фикстура — это то, что pytest готовит для теста: достаточно указать её имя
в аргументах, например async def test_...(bakery): ...
"""

import asyncio
import itertools
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# load_dotenv() в config.py не перезаписывает уже заданные переменные,
# поэтому настоящие токены из .env в тесты не попадут
TEST_ENV = {
    "BOT_TOKEN": "123456:" + "A" * 35,
    "ADMIN_IDS": "42,43",
    "DB_PATH": os.path.join(tempfile.gettempdir(), "bakery_tests_unused.db"),
    "PRIVACY_POLICY_URL": "",
    "PRIVACY_DELETION_MODE": "auto",
    "PAYMENT_TOKEN": "",
    "YOOKASSA_SHOP_ID": "",
    "YOOKASSA_SECRET_KEY": "",
}
os.environ.update(TEST_ENV)

from aiogram import Bot, Dispatcher  # noqa: E402  (импорт после настройки окружения)
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    AnswerPreCheckoutQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    SendDocument,
    SendInvoice,
    SendMessage,
    SetMyCommands,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Contact,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

from config import config  # noqa: E402
from database.db import init_db  # noqa: E402
from database.seed import seed_if_empty  # noqa: E402
from handlers import routers  # noqa: E402
from services import yookassa_api  # noqa: E402

CLIENT = 555   # Telegram ID покупателя
ADMIN = 42     # менеджеры из ADMIN_IDS
ADMIN2 = 43

# Роутер можно подключить только к одному диспетчеру, поэтому он один на все тесты.
# Память шагов оформления (FSM) сбрасывается перед каждым тестом.
DISPATCHER = Dispatcher()
DISPATCHER.include_routers(*routers)


# ---------- Имитация Telegram ----------

@dataclass
class Sent:
    """Одно действие бота: kind = send / edit / answer / invoice / delete / precheckout / document."""
    kind: str
    chat_id: int | None = None
    text: str = ""
    markup: object = None
    message_id: int | None = None
    alert: bool | None = None
    method: object = None

    def buttons(self) -> list[str]:
        rows = getattr(self.markup, "inline_keyboard", None) or getattr(self.markup, "keyboard", None) or []
        return [button.text for row in rows for button in row]


class FakeTelegram(BaseSession):
    """Вместо серверов Telegram: запоминает запросы бота и отвечает правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Sent] = []
        self.fail_invoice = False   # True — Telegram «откажет» в счёте
        self.edit_delay = 0.0       # задержка правки сообщения (имитация медленной сети)
        self._ids = itertools.count(1000)

    def _message(self, chat_id: int, text: str | None = None) -> Message:
        return Message(message_id=next(self._ids), date=datetime.now(),
                       chat=Chat(id=chat_id, type="private"), text=text)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Пекарня", username="bakery_test_bot")
        if isinstance(method, SetMyCommands):
            return True
        if isinstance(method, SendMessage):
            message = self._message(method.chat_id, method.text)
            self.calls.append(Sent("send", method.chat_id, method.text, method.reply_markup, message.message_id))
            return message
        if isinstance(method, EditMessageText):
            await asyncio.sleep(self.edit_delay)
            self.calls.append(Sent("edit", method.chat_id, method.text, method.reply_markup, method.message_id))
            return True
        if isinstance(method, AnswerCallbackQuery):
            self.calls.append(Sent("answer", text=method.text or "", alert=method.show_alert))
            return True
        if isinstance(method, SendInvoice):
            if self.fail_invoice:
                raise TelegramBadRequest(method=method, message="Bad Request: CURRENCY_TOTAL_AMOUNT_INVALID")
            message = self._message(method.chat_id)
            self.calls.append(Sent("invoice", method.chat_id, method.title, method.reply_markup,
                                   message.message_id, method=method))
            return message
        if isinstance(method, DeleteMessage):
            self.calls.append(Sent("delete", method.chat_id, message_id=method.message_id))
            return True
        if isinstance(method, AnswerPreCheckoutQuery):
            self.calls.append(Sent("precheckout", text=method.error_message or "", alert=method.ok))
            return True
        if isinstance(method, SendDocument):
            self.calls.append(Sent("document", method.chat_id, method.document.filename, method=method))
            return self._message(method.chat_id)
        raise NotImplementedError(f"FakeTelegram не умеет {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self) -> None:
        pass


# ---------- Помощник для тестов ----------

class Bakery:
    """Бот в тестовом окружении: пишем ему сообщения, жмём кнопки и смотрим, что он ответил."""

    def __init__(self, bot: Bot, telegram: FakeTelegram) -> None:
        self.bot = bot
        self.telegram = telegram
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)

    @staticmethod
    def _user(user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="Анна")

    async def _feed(self, update: Update) -> list[Sent]:
        """Отдаёт событие диспетчеру и возвращает всё, что бот сделал в ответ.

        Если запускать несколько событий одновременно (asyncio.gather), в ответ одного
        попадут и действия по другому — тогда смотрите общий список bakery.telegram.calls.
        """
        start = len(self.telegram.calls)
        await DISPATCHER.feed_update(self.bot, update)
        return self.telegram.calls[start:]

    async def send(self, user_id: int, text: str | None = None, **message_fields) -> list[Sent]:
        """Пользователь пишет сообщение (или делится контактом, или оплачивает)."""
        message = Message(message_id=next(self._message_ids), date=datetime.now(),
                          chat=Chat(id=user_id, type="private"), from_user=self._user(user_id),
                          text=text, **message_fields)
        return await self._feed(Update(update_id=next(self._update_ids), message=message))

    async def press(self, user_id: int, data: str, message_id: int = 999) -> list[Sent]:
        """Пользователь нажимает inline-кнопку с callback_data = data."""
        message = Message(message_id=message_id, date=datetime.now(),
                          chat=Chat(id=user_id, type="private"), text="…")
        query = CallbackQuery(id=str(next(self._update_ids)), from_user=self._user(user_id),
                              chat_instance="test", data=data, message=message)
        return await self._feed(Update(update_id=next(self._update_ids), callback_query=query))

    async def share_contact(self, user_id: int, phone: str) -> list[Sent]:
        contact = Contact(phone_number=phone, first_name="Анна", user_id=user_id)
        return await self.send(user_id, contact=contact)

    async def precheckout(self, user_id: int, payload: str, amount: int) -> Sent:
        """Telegram спрашивает бота: можно ли списывать деньги?"""
        query = PreCheckoutQuery(id=str(next(self._update_ids)), from_user=self._user(user_id),
                                 currency="RUB", total_amount=amount, invoice_payload=payload)
        calls = await self._feed(Update(update_id=next(self._update_ids), pre_checkout_query=query))
        return next(call for call in calls if call.kind == "precheckout")

    async def pay(self, user_id: int, payload: str, amount: int, charge: str = "1") -> list[Sent]:
        """Деньги списаны: Telegram присылает сообщение successful_payment."""
        payment = SuccessfulPayment(currency="RUB", total_amount=amount, invoice_payload=payload,
                                    telegram_payment_charge_id=f"tg-{charge}",
                                    provider_payment_charge_id=f"yk-{charge}")
        return await self.send(user_id, successful_payment=payment)

    async def checkout(self, user_id: int, *, consent: bool = True, delivery: bool = False,
                       payment: str | None = None, comment: str = "Без комментария") -> list[Sent]:
        """Проходит шаги оформления до сводки. Возвращает ответ на последний шаг."""
        await self.press(user_id, "cart:checkout")
        if consent:
            await self.press(user_id, "consent:agree")
        await self.send(user_id, "Анна")
        await self.send(user_id, "+7 900 111-22-33")
        if delivery:
            await self.send(user_id, "🚚 Доставка")
            await self.send(user_id, "ул. Мира, 1, кв. 5")
        else:
            await self.send(user_id, "🏃 Самовывоз")
        calls = await self.send(user_id, comment)
        if payment:
            calls = await self.send(user_id, payment)
        return calls

    def db(self, sql: str, *args) -> list[tuple]:
        """Прямой запрос к временной базе теста — проверить, что записалось."""
        with sqlite3.connect(config.db_path) as connection:
            return connection.execute(sql, args).fetchall()


def texts(calls: list[Sent], kind: str | None = None, chat_id: int | None = None) -> list[str]:
    """Тексты действий бота, при желании — только нужного вида и чата."""
    return [call.text for call in calls
            if (kind is None or call.kind == kind) and (chat_id is None or call.chat_id == chat_id)]


def admin_notification(calls: list[Sent], admin_id: int = ADMIN) -> Sent:
    """Уведомление менеджеру о новом заказе («🔔 Заказ №…»)."""
    return next(call for call in calls if call.kind == "send" and call.chat_id == admin_id
                and call.text.startswith("🔔"))


# ---------- Фикстуры ----------

@pytest.fixture
def settings(tmp_path):
    """Своя временная база и возможность поменять настройки на время теста:
    settings(payment_token="1:TEST:x", privacy_deletion_mode="manager")."""
    original = {field.name: getattr(config, field.name) for field in fields(config)}

    def change(**values) -> None:
        for name, value in values.items():
            # Config «заморожен» (frozen=True), чтобы бот случайно его не менял.
            # В тестах меняем осознанно и в конце теста возвращаем как было.
            object.__setattr__(config, name, value)

    change(db_path=str(tmp_path / "test.db"))
    yield change
    change(**original)


@pytest.fixture
async def bakery(settings):
    """Готовый бот: пустая база с демо-каталогом, чистая память шагов, имитация Telegram."""
    await init_db()
    await seed_if_empty()
    DISPATCHER.fsm.storage = MemoryStorage()
    telegram = FakeTelegram()
    bot = Bot(TEST_ENV["BOT_TOKEN"], session=telegram,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return Bakery(bot, telegram)


class FakeYooKassa:
    """Вместо API ЮKassa: помнит созданные платежи, статус меняем вручную."""

    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.created: list[dict] = []   # с какими параметрами бот создавал платежи
        self.fail = False               # True — ЮKassa «не отвечает»
        self._ids = itertools.count(1)

    async def create_payment(self, amount_rub, description, method, return_url, metadata):
        if self.fail:
            raise yookassa_api.YooKassaError("HTTP 500: тестовый сбой")
        payment_id = f"pay-{next(self._ids)}"
        self.created.append({"amount": amount_rub, "method": method, "return_url": return_url,
                             "metadata": metadata})
        self.payments[payment_id] = {
            "id": payment_id, "status": "pending",
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "confirmation_url": f"https://yoomoney.test/{payment_id}"},
        }
        return self.payments[payment_id]

    async def get_payment(self, payment_id):
        return self.payments[payment_id]

    def set_status(self, payment_id: str, status: str, reason: str | None = None) -> None:
        self.payments[payment_id]["status"] = status
        if reason:
            self.payments[payment_id]["cancellation_details"] = {"reason": reason}


@pytest.fixture
def yookassa(settings, monkeypatch):
    """Включает СБП (тестовый магазин) и подменяет API ЮKassa имитацией."""
    fake = FakeYooKassa()
    monkeypatch.setattr(yookassa_api, "create_payment", fake.create_payment)
    monkeypatch.setattr(yookassa_api, "get_payment", fake.get_payment)
    settings(yookassa_shop_id="123456", yookassa_secret_key="test_fake")
    return fake
