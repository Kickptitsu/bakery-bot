"""Фильтр «это менеджер?» — пропускает только тех, чей ID указан в ADMIN_IDS."""

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from config import config


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id in config.admin_ids
