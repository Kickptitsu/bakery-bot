"""Проверка того, что ввёл пользователь."""

import re


def normalize_phone(raw: str) -> str | None:
    """Приводит телефон к виду +79001234567. Возвращает None, если это не телефон.

    Принимает разные варианты записи: 8 900 123-45-67, +7 (900) 123 45 67,
    9001234567 и номер из кнопки «Поделиться контактом» (он бывает без «+»).
    """
    raw = raw.strip()
    # Разрешены только цифры, пробелы, скобки, дефисы и плюс
    if not re.fullmatch(r"[\d\s()+\-]+", raw):
        return None
    digits = re.sub(r"\D", "", raw)

    if len(digits) == 11 and digits.startswith("8"):  # 8 900 ... -> 7 900 ...
        digits = "7" + digits[1:]
    elif len(digits) == 10 and digits.startswith("9"):  # 900 ... без кода страны
        digits = "7" + digits

    if not 10 <= len(digits) <= 15:  # 15 цифр — максимум по международному стандарту
        return None
    return "+" + digits
