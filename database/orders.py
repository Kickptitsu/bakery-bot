"""Запросы к заказам."""

from aiosqlite import Row

from config import DELIVERY_PRICE, FREE_DELIVERY_FROM
from database.cart import MAX_QUANTITY
from database.db import connect

# Статусы заказа: код в базе -> подпись для людей
STATUS_LABELS = {
    "awaiting_payment": "💳 Ждёт оплаты",
    "new": "🆕 Новый",
    "cooking": "👩‍🍳 Готовится",
    "ready": "✅ Готов",
    "done": "📦 Выполнен",
    "cancelled": "❌ Отменён",
}
# Заказы, с которыми ещё нужно что-то делать.
# «Ждёт оплаты» сюда не входит: пока заказ не оплачен, менеджер его не видит.
ACTIVE_STATUSES = ("new", "cooking", "ready")
# Заказы, которые не считаются в статистике: отменённые и так и не оплаченные
NOT_COUNTED_STATUSES = ("cancelled", "awaiting_payment")
_NOT_COUNTED = ", ".join(f"'{status}'" for status in NOT_COUNTED_STATUSES)

# Куда можно перевести заказ из каждого статуса.
# «Выполнен» и «Отменён» — конечные: из них никуда.
TRANSITIONS = {
    "new": ("cooking", "cancelled"),
    "cooking": ("ready", "cancelled"),
    "ready": ("done", "cancelled"),
}


def calc_delivery_price(items_total: int, delivery_type: str) -> int:
    """Самовывоз бесплатный; доставка бесплатная от FREE_DELIVERY_FROM."""
    if delivery_type == "pickup" or items_total >= FREE_DELIVERY_FROM:
        return 0
    return DELIVERY_PRICE


async def create_order(user_id: int, info: dict) -> int | None:
    """Создаёт заказ из корзины пользователя и очищает корзину.

    info — данные из шагов оформления: customer_name, phone, delivery_type,
    address, comment, payment_method. Возвращает номер заказа или None, если
    в корзине нет товаров в наличии.

    При оплате онлайн заказ создаётся со статусом «Ждёт оплаты»: менеджер
    увидит его только после успешной оплаты (см. mark_paid).

    Всё выполняется в одной транзакции BEGIN IMMEDIATE: она сразу блокирует
    базу на запись. Если пользователь дважды быстро нажмёт «Подтвердить»,
    второй запрос дождётся первого, увидит пустую корзину и вернёт None —
    двух одинаковых заказов не будет.
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                SELECT p.id, p.name, p.price, ci.quantity
                FROM cart_items ci
                JOIN products p ON p.id = ci.product_id
                WHERE ci.user_id = ? AND p.is_available = 1
                ORDER BY p.category_id, p.sort_order, p.id
                """,
                (user_id,),
            )
            items = await cursor.fetchall()
            if not items:
                await db.rollback()
                return None

            items_total = sum(item["price"] * item["quantity"] for item in items)
            delivery_price = calc_delivery_price(items_total, info["delivery_type"])
            payment_method = info.get("payment_method", "cash")
            # Картой в Telegram и по СБП — сначала оплата, потом менеджер
            status = "new" if payment_method == "cash" else "awaiting_payment"

            cursor = await db.execute(
                """
                INSERT INTO orders (user_id, customer_name, phone, delivery_type, address,
                                    comment, items_total, delivery_price, total,
                                    payment_method, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    info["customer_name"],
                    info["phone"],
                    info["delivery_type"],
                    info.get("address"),
                    info.get("comment"),
                    items_total,
                    delivery_price,
                    items_total + delivery_price,
                    payment_method,
                    status,
                ),
            )
            order_id = cursor.lastrowid

            await db.executemany(
                "INSERT INTO order_items (order_id, product_id, name, price, quantity) "
                "VALUES (?, ?, ?, ?, ?)",
                [(order_id, i["id"], i["name"], i["price"], i["quantity"]) for i in items],
            )
            # Товары, которых нет в наличии, тоже убираем: заказ оформлен без них
            await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
            await db.commit()
            return order_id
        except Exception:
            await db.rollback()
            raise


async def get_order(order_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        return await cursor.fetchone()


async def get_order_items(order_id: int) -> list[Row]:
    async with connect() as db:
        cursor = await db.execute(
            "SELECT name, price, quantity FROM order_items WHERE order_id = ? ORDER BY id",
            (order_id,),
        )
        return await cursor.fetchall()


async def get_user_orders(user_id: int, limit: int = 10) -> list[Row]:
    """Последние заказы пользователя, новые сверху."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT id, total, status, created_at FROM orders "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return await cursor.fetchall()


async def get_last_contacts(user_id: int) -> dict:
    """Имя, телефон и адрес из прошлых заказов — чтобы предложить их кнопками.

    Возвращает словарь с ключами customer_name, phone, address (значения могут быть None).
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT customer_name, phone FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        last = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT address FROM orders WHERE user_id = ? AND delivery_type = 'delivery' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        last_delivery = await cursor.fetchone()
    return {
        "customer_name": last["customer_name"] if last else None,
        "phone": last["phone"] if last else None,
        "address": last_delivery["address"] if last_delivery else None,
    }


# ---------- Для менеджера ----------

async def get_active_orders(limit: int = 50) -> list[Row]:
    """Заказы в работе, старые сверху — их нужно делать первыми."""
    placeholders = ", ".join("?" * len(ACTIVE_STATUSES))
    async with connect() as db:
        cursor = await db.execute(
            f"SELECT id, total, status, created_at, delivery_type FROM orders "
            f"WHERE status IN ({placeholders}) ORDER BY id LIMIT ?",
            (*ACTIVE_STATUSES, limit),
        )
        return await cursor.fetchall()


async def count_active_orders() -> int:
    placeholders = ", ".join("?" * len(ACTIVE_STATUSES))
    async with connect() as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM orders WHERE status IN ({placeholders})", ACTIVE_STATUSES
        )
        (count,) = await cursor.fetchone()
        return count


async def set_status(order_id: int, new_status: str) -> bool:
    """Меняет статус, только если такой переход разрешён из ТЕКУЩЕГО статуса.

    Проверка встроена прямо в UPDATE (WHERE status IN ...), поэтому если два
    менеджера одновременно нажмут кнопки, сработает только первое нажатие,
    а второе вернёт False. Возвращает True, если статус изменён.
    """
    allowed_from = [status for status, targets in TRANSITIONS.items() if new_status in targets]
    if not allowed_from:
        return False
    placeholders = ", ".join("?" * len(allowed_from))
    async with connect() as db:
        cursor = await db.execute(
            f"UPDATE orders SET status = ?, updated_at = datetime('now', 'localtime') "
            f"WHERE id = ? AND status IN ({placeholders})",
            (new_status, order_id, *allowed_from),
        )
        await db.commit()
        return cursor.rowcount == 1


async def save_notification(order_id: int, chat_id: int, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO order_notifications (order_id, chat_id, message_id) VALUES (?, ?, ?)",
            (order_id, chat_id, message_id),
        )
        await db.commit()


async def get_notifications(order_id: int) -> list[Row]:
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id, message_id FROM order_notifications WHERE order_id = ?", (order_id,)
        )
        return await cursor.fetchall()


async def get_period_stats(since: str) -> Row:
    """Сводка по заказам, созданным начиная с since ('ГГГГ-ММ-ДД ЧЧ:ММ:СС').

    orders/revenue считаются без отменённых и неоплаченных заказов, cancelled — отдельно.
    Неоплаченные онлайн-заказы, которые клиент сам отменил, в cancelled не входят:
    менеджер их и не видел.
    """
    async with connect() as db:
        cursor = await db.execute(
            f"""
            SELECT COALESCE(SUM(status NOT IN ({_NOT_COUNTED})), 0)                     AS orders,
                   COALESCE(SUM(CASE WHEN status NOT IN ({_NOT_COUNTED}) THEN total END), 0) AS revenue,
                   COALESCE(SUM(status = 'cancelled'
                                AND NOT (payment_method != 'cash' AND paid_at IS NULL)), 0) AS cancelled
            FROM orders
            WHERE created_at >= ?
            """,
            (since,),
        )
        return await cursor.fetchone()


async def get_active_counts() -> dict[str, int]:
    """Сколько заказов сейчас в каждом активном статусе: {'new': 2, ...}."""
    async with connect() as db:
        cursor = await db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
        counts = {row["status"]: row["n"] for row in await cursor.fetchall()}
    return {status: counts.get(status, 0) for status in ACTIVE_STATUSES}


async def get_top_products(limit: int = 5) -> list[Row]:
    """Самые продаваемые товары за всё время (без отменённых и неоплаченных заказов)."""
    async with connect() as db:
        cursor = await db.execute(
            f"""
            SELECT oi.name, SUM(oi.quantity) AS quantity
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE o.status NOT IN ({_NOT_COUNTED})
            GROUP BY oi.name
            ORDER BY quantity DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()


# ---------- Онлайн-оплата ----------

async def get_items_with_stock(order_id: int) -> list[Row]:
    """Состав заказа + текущие цена и наличие товара в каталоге (для проверки перед оплатой)."""
    async with connect() as db:
        cursor = await db.execute(
            """
            SELECT oi.name, oi.price, oi.quantity,
                   p.price AS current_price, COALESCE(p.is_available, 0) AS is_available
            FROM order_items oi
            LEFT JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ?
            ORDER BY oi.id
            """,
            (order_id,),
        )
        return await cursor.fetchall()


async def set_invoice_message(order_id: int, message_id: int | None) -> int | None:
    """Запоминает сообщение со счётом. Возвращает ID предыдущего счёта (его стоит удалить)."""
    async with connect() as db:
        cursor = await db.execute("SELECT invoice_message_id FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
        await db.execute("UPDATE orders SET invoice_message_id = ? WHERE id = ?", (message_id, order_id))
        await db.commit()
        return row["invoice_message_id"] if row else None


async def mark_paid(
    order_id: int, user_id: int, amount: int, telegram_charge_id: str, provider_charge_id: str
) -> bool:
    """Записывает оплату и переводит заказ из «Ждёт оплаты» в «Новый».

    Проверка статуса прямо в UPDATE: засчитать оплату можно только один раз.
    Если заказ уже оплачен или отменён, вернётся False — платёж всё равно
    сохраняется в payments (accepted = 0), чтобы менеджер вернул деньги.
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "UPDATE orders SET status = 'new', paid_at = datetime('now', 'localtime'), "
                "updated_at = datetime('now', 'localtime') "
                "WHERE id = ? AND user_id = ? AND status = 'awaiting_payment' AND total * 100 = ?",
                (order_id, user_id, amount),
            )
            accepted = cursor.rowcount == 1
            await db.execute(
                "INSERT INTO payments (order_id, user_id, amount, telegram_charge_id, "
                "provider_charge_id, accepted) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, user_id, amount, telegram_charge_id, provider_charge_id, int(accepted)),
            )
            await db.commit()
            return accepted
        except Exception:
            await db.rollback()
            raise


async def get_accepted_payment(order_id: int) -> Row | None:
    """Засчитанная оплата заказа — номер платежа в ЮKassa нужен для возврата денег."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM payments WHERE order_id = ? AND accepted = 1", (order_id,)
        )
        return await cursor.fetchone()


async def cancel_unpaid(order_id: int, user_id: int) -> bool:
    """Клиент отменил неоплаченный заказ: товары возвращаются в корзину.

    Сработает, только если заказ всё ещё «Ждёт оплаты» — оплаченный так не отменить.
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "UPDATE orders SET status = 'cancelled', updated_at = datetime('now', 'localtime') "
                "WHERE id = ? AND user_id = ? AND status = 'awaiting_payment'",
                (order_id, user_id),
            )
            if cursor.rowcount == 0:
                await db.rollback()
                return False
            # Возвращаем в корзину то, что ещё есть в наличии (к уже лежащему там)
            await db.execute(
                """
                INSERT INTO cart_items (user_id, product_id, quantity)
                SELECT ?, oi.product_id, oi.quantity
                FROM order_items oi
                JOIN products p ON p.id = oi.product_id AND p.is_available = 1
                WHERE oi.order_id = ?
                ON CONFLICT (user_id, product_id)
                DO UPDATE SET quantity = MIN(quantity + excluded.quantity, ?)
                """,
                (user_id, order_id, MAX_QUANTITY),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise
