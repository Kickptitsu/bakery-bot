"""Мелкие функции без Telegram и базы: форматирование, проверка телефона, доставка."""

import pytest

from config import DELIVERY_PRICE, FREE_DELIVERY_FROM
from database.orders import calc_delivery_price
from utils.formatting import format_datetime, format_price, plural
from utils.validators import normalize_phone


@pytest.mark.parametrize("raw, expected", [
    ("+7 (900) 123-45-67", "+79001234567"),
    ("8 900 123 45 67", "+79001234567"),
    ("9001234567", "+79001234567"),
    ("79001234567", "+79001234567"),           # так приходит номер из «Поделиться контактом»
    ("+44 20 7946 0958", "+442079460958"),
    ("12345", None),                            # слишком короткий
    ("позвоните мне", None),
    ("+7 900 123 45 67 89 00 11", None),        # слишком длинный
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_format_price_uses_non_breaking_spaces():
    assert format_price(1500) == "1 500 ₽"
    assert format_price(70) == "70 ₽"


@pytest.mark.parametrize("n, expected", [
    (1, "1 заказ"), (3, "3 заказа"), (5, "5 заказов"),
    (11, "11 заказов"), (21, "21 заказ"), (22, "22 заказа"), (112, "112 заказов"),
])
def test_plural(n, expected):
    assert plural(n, "заказ", "заказа", "заказов") == expected


def test_format_datetime():
    assert format_datetime("2026-10-25 17:40:12") == "25.10.2026 17:40"


def test_delivery_price():
    assert calc_delivery_price(100, "pickup") == 0
    assert calc_delivery_price(FREE_DELIVERY_FROM - 1, "delivery") == DELIVERY_PRICE
    assert calc_delivery_price(FREE_DELIVERY_FROM, "delivery") == 0
