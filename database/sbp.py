"""Запросы к таблице платежей по СБП (sbp_payments)."""

from aiosqlite import Row

from database.db import connect

# Ссылка на оплату СБП живёт 1 час. Отправляем клиенту старую ссылку повторно,
# только если ей меньше 55 минут, иначе создаём новый платёж.
LINK_REUSE_MINUTES = 55


async def save_payment(payment_id: str, order_id: int, user_id: int, confirmation_url: str) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO sbp_payments (id, order_id, user_id, confirmation_url) VALUES (?, ?, ?, ?)",
            (payment_id, order_id, user_id, confirmation_url),
        )
        await db.commit()


async def get_fresh_pending(order_id: int) -> Row | None:
    """Неоплаченная ссылка на этот заказ, которой ещё можно пользоваться."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM sbp_payments WHERE order_id = ? AND status = 'pending' "
            "AND created_at >= datetime('now', 'localtime', ?) ORDER BY created_at DESC LIMIT 1",
            (order_id, f"-{LINK_REUSE_MINUTES} minutes"),
        )
        return await cursor.fetchone()


async def get_all_pending() -> list[Row]:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM sbp_payments WHERE status = 'pending' ORDER BY created_at")
        return await cursor.fetchall()


async def finish(payment_id: str, status: str) -> bool:
    """pending -> succeeded/canceled. Проверка в UPDATE: один платёж обрабатывается один раз."""
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE sbp_payments SET status = ? WHERE id = ? AND status = 'pending'", (status, payment_id)
        )
        await db.commit()
        return cursor.rowcount == 1
