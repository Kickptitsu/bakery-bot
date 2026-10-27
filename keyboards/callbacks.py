"""Данные, которые «зашиты» в inline-кнопки.

Telegram передаёт боту строку callback_data (до 64 байт). Классы CallbackData
сами упаковывают поля в строку вида "qty:5:1:card" и распаковывают обратно,
а в хендлерах можно фильтровать по ним: QtyCB.filter(F.source == "card").
"""

from aiogram.filters.callback_data import CallbackData

# Простые кнопки без параметров
CATALOG = "catalog"      # вернуться к списку категорий
MY_ORDERS = "my_orders"  # вернуться к списку своих заказов
NOOP = "noop"            # кнопка-надпись, нажатие ничего не делает
POLICY = "policy"        # прислать политику обработки ПД файлом (если ссылка не задана)


class CategoryCB(CallbackData, prefix="cat"):
    """Открыть категорию (список товаров)."""
    id: int


class ProductCB(CallbackData, prefix="prod"):
    """Открыть карточку товара."""
    id: int


class QtyCB(CallbackData, prefix="qty"):
    """Изменить количество товара в корзине."""
    product_id: int
    delta: int   # +1 или -1
    source: str  # где нажали: "card" (карточка) или "cart" (корзина) — что перерисовать


class CartCB(CallbackData, prefix="cart"):
    """Действия с корзиной: open / clear / checkout."""
    action: str


class CheckoutCB(CallbackData, prefix="checkout"):
    """Кнопки итоговой сводки: confirm / cancel."""
    action: str


class OrderCB(CallbackData, prefix="order"):
    """Открыть свой заказ в «Моих заказах»."""
    id: int


class PayCB(CallbackData, prefix="pay"):
    """Неоплаченный заказ: invoice (прислать счёт заново) / cancel (отменить заказ)."""
    order_id: int
    action: str


class ConsentCB(CallbackData, prefix="consent"):
    """Ответ на запрос согласия: agree / decline."""
    action: str


class PrivacyCB(CallbackData, prefix="privacy"):
    """Кнопки /privacy: delete_ask (спросить «точно?») / delete_confirm / back."""
    action: str


# ---------- Кнопки менеджера ----------

class AdminCB(CallbackData, prefix="adm"):
    """Разделы /admin: menu / orders / stock / stats / privacy, а также privacy_log (выгрузка CSV)."""
    section: str


class AdminPrivacyCB(CallbackData, prefix="adm_privacy"):
    """Запрос на удаление данных: open / approve / reject."""
    id: int
    action: str


class AdminOrderCB(CallbackData, prefix="adm_order"):
    """Действия с заказом: open (показать карточку) / cancel_ask (спросить «точно отменить?»)."""
    id: int
    action: str
    from_list: bool  # открыт из списка в /admin — тогда показываем кнопку «К списку»


class AdminStatusCB(CallbackData, prefix="adm_status"):
    """Сменить статус заказа."""
    id: int
    status: str
    from_list: bool


class AdminStockCategoryCB(CallbackData, prefix="adm_stock_cat"):
    """Открыть категорию в управлении наличием."""
    id: int


class AdminToggleCB(CallbackData, prefix="adm_toggle"):
    """Переключить наличие товара."""
    product_id: int
