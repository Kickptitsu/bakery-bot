"""Мелкие помощники для красивого вывода."""

from datetime import datetime


def format_price(rubles: int) -> str:
    """1500 -> '1 500 ₽' (неразрывный пробел, чтобы число не переносилось)."""
    return f"{rubles:,}".replace(",", " ") + " ₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Склонение по числу: plural(3, 'заказ', 'заказа', 'заказов') -> '3 заказа'."""
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def format_datetime(value: str) -> str:
    """'2026-10-25 17:40:12' (так SQLite хранит дату) -> '25.10.2026 17:40'."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
