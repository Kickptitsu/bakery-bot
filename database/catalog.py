"""Запросы к каталогу: категории и товары."""

from aiosqlite import Row

from database.db import connect


async def get_categories() -> list[Row]:
    """Все категории вместе с количеством товаров в наличии."""
    async with connect() as db:
        cursor = await db.execute(
            """
            SELECT c.id, c.name,
                   COUNT(p.id) AS available_count
            FROM categories c
            LEFT JOIN products p
                   ON p.category_id = c.id AND p.is_available = 1
            GROUP BY c.id
            ORDER BY c.sort_order, c.id
            """
        )
        return await cursor.fetchall()


async def get_category(category_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute("SELECT id, name FROM categories WHERE id = ?", (category_id,))
        return await cursor.fetchone()


async def get_products(category_id: int, only_available: bool = True) -> list[Row]:
    """Товары категории. Клиенту показываем только те, что в наличии."""
    query = "SELECT * FROM products WHERE category_id = ?"
    if only_available:
        query += " AND is_available = 1"
    query += " ORDER BY sort_order, id"
    async with connect() as db:
        cursor = await db.execute(query, (category_id,))
        return await cursor.fetchall()


async def get_product(product_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        return await cursor.fetchone()


async def toggle_availability(product_id: int) -> bool | None:
    """Переключает «в наличии / нет». Возвращает новое значение или None, если товара нет.

    1 - is_available считается в самом SQL, поэтому быстрые нажатия не собьют значение.
    """
    async with connect() as db:
        await db.execute(
            "UPDATE products SET is_available = 1 - is_available WHERE id = ?", (product_id,)
        )
        await db.commit()
        cursor = await db.execute("SELECT is_available FROM products WHERE id = ?", (product_id,))
        row = await cursor.fetchone()
        return bool(row["is_available"]) if row else None
