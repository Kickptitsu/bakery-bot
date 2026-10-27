"""Минимальный клиент API ЮKassa: создать платёж и узнать его статус.

Нужен для оплаты по СБП: во встроенном счёте Telegram СБП нет, поэтому
бот сам создаёт платёж в ЮKassa и присылает клиенту ссылку на оплату.

Документация: https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process
"""

import uuid

import aiohttp

from config import config

API_URL = "https://api.yookassa.ru/v3/payments"
TIMEOUT = aiohttp.ClientTimeout(total=15)


class YooKassaError(Exception):
    """ЮKassa вернула ошибку или не ответила."""


def is_configured() -> bool:
    return bool(config.yookassa_shop_id and config.yookassa_secret_key)


def is_test_shop() -> bool:
    # Секретный ключ тестового магазина ЮKassa начинается с test_, боевого — с live_
    return config.yookassa_secret_key.startswith("test_")


def _auth() -> aiohttp.BasicAuth:
    # ЮKassa проверяет запросы по паре «ID магазина : секретный ключ»
    return aiohttp.BasicAuth(config.yookassa_shop_id, config.yookassa_secret_key)


async def _request(method: str, url: str, **kwargs) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, auth=_auth()) as session:
            async with session.request(method, url, **kwargs) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    # В ответе об ошибке ключей нет — description можно спокойно писать в лог
                    raise YooKassaError(f"HTTP {response.status}: {data.get('description', data)}")
                return data
    except (aiohttp.ClientError, TimeoutError) as error:
        raise YooKassaError(f"нет ответа от ЮKassa: {error!r}") from error


async def create_payment(
    amount_rub: int, description: str, method: str, return_url: str, metadata: dict
) -> dict:
    """Создаёт одностадийный платёж и возвращает его (в нём есть confirmation_url).

    method — способ оплаты: 'sbp' или 'bank_card' (в тестовом магазине СБП недоступен).
    """
    body = {
        "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
        "capture": True,  # списать сразу, без второй стадии
        "payment_method_data": {"type": method},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],  # ограничение ЮKassa
        "metadata": metadata,
    }
    # Idempotence-Key защищает от дублей: если запрос повторится из-за сбоя сети,
    # ЮKassa вернёт тот же платёж, а не создаст второй
    headers = {"Idempotence-Key": str(uuid.uuid4())}
    return await _request("POST", API_URL, json=body, headers=headers)


async def get_payment(payment_id: str) -> dict:
    """Текущее состояние платежа: status = pending / succeeded / canceled."""
    return await _request("GET", f"{API_URL}/{payment_id}")
