import logging
from typing import List, Optional
from datetime import datetime, time

logger = logging.getLogger(__name__)

TRIGGERS: List[str] = ["котра година", "скільки часу", "яка година", "скажи час", "котра зараз година"]

MAX_RETRIES: int = 3

def _get_current_time() -> str:
    """Отримує поточний час і повертає його у форматі рядка.

    Returns:
        str: Поточний час у форматі "години:хвилини".
    """
    now: datetime = datetime.now()
    current_time: time = now.time()
    hours: int = current_time.hour
    minutes: int = current_time.minute
    return f"{hours:02d}:{minutes:02d}"

def _format_time_response(time_str: str) -> str:
    """Форматує рядок часу у відповідь для голосового асистента.

    Args:
        time_str: Рядок часу у форматі "години:хвилини".

    Returns:
        str: Відформатована відповідь українською мовою.
    """
    try:
        hours_str: str
        minutes_str: str
        hours_str, minutes_str = time_str.split(":")
        hours: int = int(hours_str)
        minutes: int = int(minutes_str)
        if hours == 0:
            hour_word: str = "дванадцять"
        elif hours <= 12:
            hour_word = str(hours)
        else:
            hour_word = str(hours - 12)
        if minutes == 0:
            return f"Зараз рівно {hour_word} година"
        elif minutes < 10:
            return f"Зараз {hour_word} година нуль {minutes} хвилин"
        else:
            return f"Зараз {hour_word} година {minutes} хвилин"
    except ValueError:
        logger.error("Помилка форматування часу: некоректний рядок %s", time_str)
        return "Вибачте, сталася помилка при визначенні часу"

def execute(cmd: str, jarvis) -> str:
    """Виконує команду визначення поточного часу.

    Args:
        cmd: Рядок команди від користувача.
        jarvis: Об'єкт голосового асистента для додаткових операцій.

    Returns:
        str: Відповідь із поточним часом або повідомлення про помилку.

    Raises:
        ValueError: Якщо cmd порожній після видалення пробілів.
    """
    cleaned_cmd: str = cmd.strip()
    if not cleaned_cmd:
        raise ValueError("Команда не може бути порожньою")
    try:
        current_time: str = _get_current_time()
        response: str = _format_time_response(current_time)
        return response
    except OSError as e:
        logger.error("Системна помилка при отриманні часу: %s", e)
        return "Вибачте, не вдалося отримати поточний час через системну помилку"
    except ValueError as e:
        logger.error("Помилка значення при обробці часу: %s", e)
        return "Вибачте, сталася помилка при обробці часу"