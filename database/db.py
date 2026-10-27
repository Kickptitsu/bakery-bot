"""Подключение к SQLite и создание таблиц."""

from contextlib import asynccontextmanager

import aiosqlite

from config import config

# Схема базы. CREATE TABLE IF NOT EXISTS не трогает уже созданные таблицы,
# поэтому init_db() можно спокойно вызывать при каждом запуске.
SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name         TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    price        INTEGER NOT NULL,             -- цена в рублях
    is_available INTEGER NOT NULL DEFAULT 1,   -- 1 = в наличии, 0 = нет
    sort_order   INTEGER NOT NULL DEFAULT 0
);

-- Корзина: одна строка = один товар у одного пользователя
CREATE TABLE IF NOT EXISTS cart_items (
    user_id    INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,  -- он же номер заказа
    user_id        INTEGER NOT NULL,
    customer_name  TEXT    NOT NULL,
    phone          TEXT    NOT NULL,
    delivery_type  TEXT    NOT NULL,                   -- 'pickup' или 'delivery'
    address        TEXT,                               -- только для доставки
    comment        TEXT,
    items_total    INTEGER NOT NULL,                   -- сумма за товары
    delivery_price INTEGER NOT NULL,
    total          INTEGER NOT NULL,                   -- к оплате
    status         TEXT    NOT NULL DEFAULT 'new',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Состав заказа. Название и цену копируем, а не берём из products:
-- если позже цена изменится, в старом заказе останется та, по которой купили.
CREATE TABLE IF NOT EXISTS order_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER,
    name       TEXT    NOT NULL,
    price      INTEGER NOT NULL,
    quantity   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);

-- Платежи по СБП, созданные через API ЮKassa. Пока status = 'pending', бот
-- раз в несколько секунд спрашивает у ЮKassa, оплатил ли клиент.
CREATE TABLE IF NOT EXISTS sbp_payments (
    id               TEXT    PRIMARY KEY,          -- ID платежа в ЮKassa
    order_id         INTEGER NOT NULL,
    user_id          INTEGER NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending',  -- pending / succeeded / canceled
    confirmation_url TEXT    NOT NULL,             -- ссылка на оплату для клиента
    created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Каждая успешная онлайн-оплата, даже повторная (её нужно вернуть клиенту).
-- accepted = 1 — оплата засчитана заказу, 0 — заказ уже был оплачен или отменён.
CREATE TABLE IF NOT EXISTS payments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id           INTEGER NOT NULL,
    user_id            INTEGER NOT NULL,
    amount             INTEGER NOT NULL,           -- в копейках, как присылает Telegram
    telegram_charge_id TEXT    NOT NULL,           -- номер платежа в Telegram
    provider_charge_id TEXT    NOT NULL,           -- номер платежа в ЮKassa (для возврата)
    accepted           INTEGER NOT NULL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Какие сообщения-уведомления о заказе получили менеджеры. Когда один менеджер
-- меняет статус, бот обновляет эти сообщения у всех, чтобы статус везде был свежий.
CREATE TABLE IF NOT EXISTS order_notifications (
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

-- ---------- Персональные данные ----------

-- Согласия на обработку ПД. Строки не удаляются: при отзыве ставится revoked_at.
-- Так остаётся доказательство, когда и на какую версию текста согласился человек.
CREATE TABLE IF NOT EXISTS consents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    version     TEXT    NOT NULL,                  -- версия текста согласия (CONSENT_VERSION)
    accepted_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    revoked_at  TEXT                               -- NULL, пока согласие действует
);
CREATE INDEX IF NOT EXISTS idx_consents_user ON consents(user_id);

-- Запросы «отозвать согласие и удалить данные».
-- status: new -> waiting_orders (ждём завершения заказов) / waiting_manager (ждём решения)
--         -> done (обезличено) / rejected (менеджер отклонил)
CREATE TABLE IF NOT EXISTS deletion_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'new',
    requested_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    processed_at TEXT,
    processed_by TEXT                              -- 'auto' или 'manager:<ID>'
);

-- Журнал событий с персональными данными: чтобы заказчик мог подтвердить,
-- что выполнил требование. В details не пишем номера заказов — иначе по журналу
-- можно было бы снова связать обезличенные заказы с человеком.
CREATE TABLE IF NOT EXISTS privacy_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id    INTEGER NOT NULL,
    event      TEXT    NOT NULL,
    details    TEXT    NOT NULL DEFAULT '',
    actor      TEXT    NOT NULL                    -- 'user', 'auto' или 'manager:<ID>'
);

-- Нумерация заказов начинается с 1001, а не с 1 — так солиднее.
-- sqlite_sequence — служебная таблица SQLite со счётчиками AUTOINCREMENT.
INSERT INTO sqlite_sequence (name, seq)
SELECT 'orders', 1000
WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'orders');
"""


@asynccontextmanager
async def connect():
    """Открывает соединение с базой. Использование:

    async with connect() as db:
        await db.execute(...)
    """
    async with aiosqlite.connect(config.db_path) as db:
        # Строки результата можно читать по имени колонки: row["name"]
        db.row_factory = aiosqlite.Row
        # В SQLite внешние ключи по умолчанию выключены
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


# Колонки, которые появились в уже существующих таблицах позже первой версии.
# CREATE TABLE IF NOT EXISTS не добавляет колонки в созданную таблицу,
# поэтому init_db() дописывает недостающие через ALTER TABLE — данные не теряются.
MIGRATIONS = {
    "orders": {
        "payment_method": "TEXT NOT NULL DEFAULT 'cash'",  # 'cash' — при получении, 'online' — картой
        "paid_at": "TEXT",                                  # когда пришла онлайн-оплата
        "invoice_message_id": "INTEGER",                    # сообщение со счётом (чтобы удалить старый)
    },
}


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет, и добавляет недостающие колонки."""
    async with connect() as db:
        await db.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            cursor = await db.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cursor.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await db.commit()
