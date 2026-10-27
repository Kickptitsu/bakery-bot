"""Сборка всех роутеров в одном месте.

Порядок важен: aiogram проверяет роутеры сверху вниз и отдаёт апдейт первому
подходящему хендлеру:
- payments — сразу после start: сообщение об успешной оплате должно обработаться,
  даже если клиент в этот момент уже оформляет следующий заказ;
- admin и privacy стоят до checkout, чтобы /admin и /privacy работали, даже если
  пользователь посреди оформления заказа (иначе команда сохранилась бы как имя);
- checkout — до common, чтобы ответы на шагах оформления не ушли в common;
- common (ловит «всё остальное») — последний.
"""

from handlers import admin, cart, catalog, checkout, common, orders, payments, privacy, start

routers = [
    start.router,
    payments.router,
    catalog.router,
    cart.router,
    admin.router,
    privacy.router,
    checkout.router,
    orders.router,
    common.router,
]
