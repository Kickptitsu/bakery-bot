"""Настройки бота. Всё секретное читается из файла .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Подхватываем переменные из .env, лежащего рядом с этим файлом
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    db_path: str
    privacy_policy_url: str      # ссылка на опубликованную политику ПД ('' — не задана)
    privacy_deletion_mode: str   # 'auto' — обезличивать сразу, 'manager' — решает менеджер
    payment_token: str           # токен платёжного провайдера от @BotFather ('' — онлайн-оплата выключена)
    yookassa_shop_id: str        # ID магазина ЮKassa для оплаты по СБП через API ('' — СБП выключен)
    yookassa_secret_key: str     # секретный ключ магазина ЮKassa (test_... или live_...)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example")

    # "111, 222" -> (111, 222); пустые куски пропускаем
    raw_ids = os.getenv("ADMIN_IDS", "")
    admin_ids = tuple(int(part) for part in raw_ids.replace(" ", "").split(",") if part)

    db_path = os.getenv("DB_PATH", "bakery.db")
    # Относительный путь считаем от папки проекта, а не от места запуска
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(__file__), db_path)

    deletion_mode = os.getenv("PRIVACY_DELETION_MODE", "auto").strip().lower()
    if deletion_mode not in ("auto", "manager"):
        raise RuntimeError("PRIVACY_DELETION_MODE в .env должен быть auto или manager")
    if deletion_mode == "manager" and not admin_ids:
        raise RuntimeError("PRIVACY_DELETION_MODE=manager требует хотя бы один ID в ADMIN_IDS")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=db_path,
        privacy_policy_url=os.getenv("PRIVACY_POLICY_URL", "").strip(),
        privacy_deletion_mode=deletion_mode,
        payment_token=os.getenv("PAYMENT_TOKEN", "").strip(),
        yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", "").strip(),
        yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
    )


config = load_config()

# Условия доставки (в рублях). Это не секреты, поэтому хранятся в коде, а не в .env
DELIVERY_PRICE = 200
FREE_DELIVERY_FROM = 1500

# Онлайн-оплату предлагаем только от этой суммы. У Telegram есть минимум для оплаты
# (около 1 $, в рублях он меняется вместе с курсом) — берём с запасом.
MIN_ONLINE_PAYMENT = 100
