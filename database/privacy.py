"""Запросы для персональных данных: согласия, запросы на удаление, журнал."""

from aiosqlite import Connection, Row

from database.db import connect
from database.orders import ACTIVE_STATUSES

# Чем заменяем имя, телефон и адрес при обезличивании
ANONYMIZED = "[удалено]"
# user_id обезличенных заказов: заказ больше не связан ни с каким пользователем
ANONYMOUS_USER_ID = 0

# Запрос ещё не выполнен и не отклонён
PENDING_STATUSES = ("new", "waiting_orders", "waiting_manager")

# Подписи событий журнала — для людей и для выгрузки в CSV
EVENT_LABELS = {
    "consent_given": "Согласие дано",
    "consent_revoked": "Согласие отозвано",
    "deletion_requested": "Запрос на удаление данных",
    "deletion_postponed": "Удаление отложено до завершения заказов",
    "sent_to_manager": "Запрос передан менеджеру",
    "anonymized": "Данные обезличены",
    "rejected": "Запрос отклонён менеджером",
}

_ACTIVE = ", ".join(f"'{status}'" for status in ACTIVE_STATUSES)
_PENDING = ", ".join(f"'{status}'" for status in PENDING_STATUSES)


async def _log(db: Connection, user_id: int, event: str, actor: str, details: str = "") -> None:
    """Запись в журнал внутри уже открытого соединения (в той же транзакции)."""
    await db.execute(
        "INSERT INTO privacy_log (user_id, event, details, actor) VALUES (?, ?, ?, ?)",
        (user_id, event, details, actor),
    )


async def log_event(user_id: int, event: str, actor: str, details: str = "") -> None:
    async with connect() as db:
        await _log(db, user_id, event, actor, details)
        await db.commit()


# ---------- Согласие ----------

async def get_last_consent(user_id: int) -> Row | None:
    """Последнее согласие пользователя (действующее или отозванное)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM consents WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        )
        return await cursor.fetchone()


async def has_consent(user_id: int, version: str) -> bool:
    """Есть ли действующее согласие на ТЕКУЩУЮ версию текста.

    Если текст согласия поменяли (и версию вместе с ним), спросим заново.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM consents WHERE user_id = ? AND version = ? AND revoked_at IS NULL",
            (user_id, version),
        )
        return await cursor.fetchone() is not None


async def give_consent(user_id: int, version: str) -> bool:
    """Сохраняет согласие. Повторное нажатие «Согласен» не создаёт дубль.

    Возвращает True, если согласие записано сейчас.
    """
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO consents (user_id, version)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM consents WHERE user_id = ? AND version = ? AND revoked_at IS NULL
            )
            """,
            (user_id, version, user_id, version),
        )
        created = cursor.rowcount == 1
        if created:
            await _log(db, user_id, "consent_given", "user", f"версия текста: {version}")
        await db.commit()
        return created


# ---------- Что хранится о пользователе ----------

async def get_user_data(user_id: int) -> dict:
    """Все персональные данные пользователя — для /privacy и для менеджера."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, status, customer_name, phone, address, comment FROM orders "
            "WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        orders = await cursor.fetchall()
        cursor = await db.execute("SELECT COUNT(*) FROM cart_items WHERE user_id = ?", (user_id,))
        (cart_items,) = await cursor.fetchone()

    def unique(field: str) -> list[str]:
        # Без повторов, в порядке появления
        return list(dict.fromkeys(order[field] for order in orders if order[field]))

    return {
        "order_ids": [order["id"] for order in orders],
        "active_order_ids": [order["id"] for order in orders if order["status"] in ACTIVE_STATUSES],
        "unpaid_order_ids": [order["id"] for order in orders if order["status"] == "awaiting_payment"],
        "names": unique("customer_name"),
        "phones": unique("phone"),
        "addresses": unique("address"),
        "comments": sum(1 for order in orders if order["comment"]),
        "cart_items": cart_items,
    }


# ---------- Запросы на удаление ----------

async def get_pending_request(user_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute(
            f"SELECT * FROM deletion_requests WHERE user_id = ? AND status IN ({_PENDING}) "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return await cursor.fetchone()


async def get_request(request_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM deletion_requests WHERE id = ?", (request_id,))
        return await cursor.fetchone()


async def get_requests(status: str) -> list[Row]:
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM deletion_requests WHERE status = ? ORDER BY id", (status,)
        )
        return await cursor.fetchall()


async def create_deletion_request(user_id: int) -> int | None:
    """Отзывает согласие и создаёт запрос на удаление — одной транзакцией.

    Возвращает номер запроса или None, если у пользователя уже есть
    незавершённый запрос (например, двойное нажатие кнопки).
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                f"SELECT 1 FROM deletion_requests WHERE user_id = ? AND status IN ({_PENDING})",
                (user_id,),
            )
            if await cursor.fetchone():
                await db.rollback()
                return None

            cursor = await db.execute("INSERT INTO deletion_requests (user_id) VALUES (?)", (user_id,))
            request_id = cursor.lastrowid

            # Неоплаченные онлайн-заказы отменяем сразу: после отзыва согласия
            # оплатить их уже нельзя (pre_checkout не пропустит отменённый заказ)
            await db.execute(
                "UPDATE orders SET status = 'cancelled', updated_at = datetime('now', 'localtime') "
                "WHERE user_id = ? AND status = 'awaiting_payment'",
                (user_id,),
            )

            # Отзыв согласия действует сразу, даже если удаление придётся отложить
            cursor = await db.execute(
                "UPDATE consents SET revoked_at = datetime('now', 'localtime') "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
            if cursor.rowcount:
                await _log(db, user_id, "consent_revoked", "user")
            await _log(db, user_id, "deletion_requested", "user", f"запрос №{request_id}")
            await db.commit()
            return request_id
        except Exception:
            await db.rollback()
            raise


async def set_request_status(
    request_id: int, status: str, allowed_from: tuple[str, ...], actor: str | None = None
) -> bool:
    """Меняет статус запроса, только если сейчас он в одном из allowed_from.

    Как и со статусами заказов: проверка прямо в UPDATE, поэтому из двух
    одновременных решений менеджеров сработает только одно.
    """
    placeholders = ", ".join("?" * len(allowed_from))
    final = status in ("done", "rejected")
    async with connect() as db:
        cursor = await db.execute(
            f"""
            UPDATE deletion_requests
            SET status = ?,
                processed_at = CASE WHEN ? THEN datetime('now', 'localtime') ELSE processed_at END,
                processed_by = COALESCE(?, processed_by)
            WHERE id = ? AND status IN ({placeholders})
            """,
            (status, final, actor, request_id, *allowed_from),
        )
        await db.commit()
        return cursor.rowcount == 1


async def anonymize_user(user_id: int, request_id: int, actor: str) -> list[int] | None:
    """Обезличивает данные пользователя и закрывает запрос — одной транзакцией.

    - в заказах имя, телефон и адрес заменяются на «[удалено]», комментарий
      удаляется, user_id обнуляется (заказ больше не связан с человеком);
      суммы и состав остаются для статистики;
    - корзина удаляется;
    - в журнал пишется, что именно сделано.

    Возвращает номера обезличенных заказов (может быть пустой список) или None,
    если обезличить нельзя: есть незавершённые заказы или запрос уже обработан.
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                f"SELECT 1 FROM orders WHERE user_id = ? AND status IN ({_ACTIVE})", (user_id,)
            )
            if await cursor.fetchone():
                await db.rollback()
                return None

            cursor = await db.execute(
                "UPDATE deletion_requests SET status = 'done', "
                "processed_at = datetime('now', 'localtime'), processed_by = ? "
                f"WHERE id = ? AND user_id = ? AND status IN ({_PENDING})",
                (actor, request_id, user_id),
            )
            if cursor.rowcount == 0:
                await db.rollback()
                return None

            # Неоплаченные онлайн-заказы отменяем: оплатить их после удаления данных уже нельзя
            await db.execute(
                "UPDATE orders SET status = 'cancelled' WHERE user_id = ? AND status = 'awaiting_payment'",
                (user_id,),
            )
            cursor = await db.execute("SELECT id FROM orders WHERE user_id = ?", (user_id,))
            order_ids = [row["id"] for row in await cursor.fetchall()]
            # Записи об оплатах остаются для бухгалтерии, но без привязки к человеку
            for table in ("payments", "sbp_payments"):
                await db.execute(
                    f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (ANONYMOUS_USER_ID, user_id)
                )
            await db.execute(
                """
                UPDATE orders
                SET customer_name = ?, phone = ?,
                    address = CASE WHEN address IS NULL THEN NULL ELSE ? END,
                    comment = NULL,
                    user_id = ?
                WHERE user_id = ?
                """,
                (ANONYMIZED, ANONYMIZED, ANONYMIZED, ANONYMOUS_USER_ID, user_id),
            )
            cursor = await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
            cart_deleted = cursor.rowcount

            if order_ids:
                orders_part = (
                    f"обезличено заказов — {len(order_ids)} (имя, телефон, адрес и комментарий "
                    "удалены, заказы отвязаны от Telegram ID)"
                )
            else:
                orders_part = "заказов не было"
            await _log(
                db, user_id, "anonymized", actor,
                f"запрос №{request_id}: {orders_part}; удалено позиций корзины — {cart_deleted}",
            )
            await db.commit()
            return order_ids
        except Exception:
            await db.rollback()
            raise


async def get_log() -> list[Row]:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM privacy_log ORDER BY id")
        return await cursor.fetchall()
