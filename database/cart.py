"""Запросы к корзине."""

from aiosqlite import Row

from database.db import connect

# Ограничение, чтобы случайно не «натыкать» 500 круассанов
MAX_QUANTITY = 50


async def get_quantity(user_id: int, product_id: int) -> int:
    """Сколько штук товара сейчас в корзине (0, если нет)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
        row = await cursor.fetchone()
        return row["quantity"] if row else 0


async def get_quantities(user_id: int) -> dict[int, int]:
    """Вся корзина в виде {id товара: количество} — для пометок в списке товаров."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT product_id, quantity FROM cart_items WHERE user_id = ?", (user_id,)
        )
        return {row["product_id"]: row["quantity"] for row in await cursor.fetchall()}


async def change_quantity(user_id: int, product_id: int, delta: int) -> int:
    """Увеличивает (delta > 0) или уменьшает (delta < 0) количество товара.

    Если количество стало 0 — товар удаляется из корзины.
    Возвращает новое количество.

    Количество меняется прямо в SQL (quantity = quantity + delta), а не
    «прочитали -> посчитали в Python -> записали». Иначе два быстрых нажатия,
    обработанные одновременно, могли бы засчитаться как одно.
    """
    async with connect() as db:
        if delta > 0:
            # Вставляем строку, а если она уже есть — прибавляем (не больше MAX_QUANTITY)
            await db.execute(
                """
                INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, MIN(?, ?))
                ON CONFLICT (user_id, product_id)
                DO UPDATE SET quantity = MIN(quantity + excluded.quantity, ?)
                """,
                (user_id, product_id, delta, MAX_QUANTITY, MAX_QUANTITY),
            )
        else:
            # Уменьшаем, пока остаётся больше нуля...
            cursor = await db.execute(
                "UPDATE cart_items SET quantity = quantity + ? "
                "WHERE user_id = ? AND product_id = ? AND quantity + ? > 0",
                (delta, user_id, product_id, delta),
            )
            # ...а если должно стать 0 или меньше — удаляем товар из корзины
            if cursor.rowcount == 0:
                await db.execute(
                    "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
                    (user_id, product_id),
                )
        await db.commit()

        cursor = await db.execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
        row = await cursor.fetchone()
        return row["quantity"] if row else 0


async def get_cart(user_id: int) -> list[Row]:
    """Содержимое корзины вместе с названиями, ценами и наличием товаров."""
    async with connect() as db:
        cursor = await db.execute(
            """
            SELECT p.id AS product_id, p.name, p.price, p.is_available,
                   ci.quantity,
                   p.price * ci.quantity AS subtotal
            FROM cart_items ci
            JOIN products p ON p.id = ci.product_id
            WHERE ci.user_id = ?
            ORDER BY p.category_id, p.sort_order, p.id
            """,
            (user_id,),
        )
        return await cursor.fetchall()


async def clear_cart(user_id: int) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
        await db.commit()
