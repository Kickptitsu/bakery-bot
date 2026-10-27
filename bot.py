"""Точка входа. Запуск: python bot.py"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat

from config import config
from database.db import init_db
from database.seed import seed_if_empty
from handlers import routers
from services import payments, sbp, yookassa_api


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # Готовим базу: создаём таблицы и заполняем каталог при первом запуске
    await init_db()
    await seed_if_empty()

    # Никогда не печатаем сам токен — только включена ли оплата и в каком режиме
    if not payments.is_enabled():
        logging.info("Онлайн-оплата выключена: PAYMENT_TOKEN не задан в .env")
    elif payments.is_test_mode():
        logging.info("Онлайн-оплата включена: ТЕСТОВЫЙ режим ЮKassa, деньги не списываются")
    else:
        logging.warning("Онлайн-оплата включена в БОЕВОМ режиме: платежи настоящие")

    if not sbp.is_enabled():
        logging.info("Оплата по СБП выключена: YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY не заданы в .env")
    elif yookassa_api.is_test_shop():
        logging.info("Оплата по СБП включена: ТЕСТОВЫЙ магазин ЮKassa (вместо СБП — тестовая карта)")
    else:
        logging.warning("Оплата по СБП включена в БОЕВОМ магазине ЮKassa: платежи настоящие")

    if not config.privacy_policy_url:
        logging.warning(
            "PRIVACY_POLICY_URL не задан: вместо ссылки бот будет присылать файл PRIVACY.md. "
            "Заполните шаблон, опубликуйте политику и укажите ссылку в .env"
        )

    # parse_mode=HTML: в текстах можно использовать <b>жирный</b>, <s>зачёркнутый</s> и т.п.
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(*routers)

    # Команды в меню «/» рядом с полем ввода
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="privacy", description="Мои данные и согласие"),
    ]
    await bot.set_my_commands(commands)
    # Менеджерам — ещё и /admin (видно только в их личных чатах с ботом)
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                [*commands, BotCommand(command="admin", description="Панель менеджера")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramAPIError as error:
            # Обычно значит, что менеджер ещё ни разу не открывал бота
            logging.warning("Не удалось задать команды менеджеру %s: %s", admin_id, error)

    # Пропускаем сообщения, которые пришли, пока бот был выключен
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Бот запущен. Остановить: Ctrl+C")
    # Фоновая задача: раз в несколько секунд спрашивает ЮKassa об оплатах по СБП
    sbp_watcher = asyncio.create_task(sbp.watch_payments(bot)) if sbp.is_enabled() else None
    try:
        await dp.start_polling(bot)
    finally:
        if sbp_watcher:
            sbp_watcher.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
