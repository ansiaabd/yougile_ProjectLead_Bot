import re
from datetime import datetime, timedelta
from typing import Optional

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
    "01": 1, "02": 2, "03": 3, "04": 4, "05": 5, "06": 6,
    "07": 7, "08": 8, "09": 9, "10": 10, "11": 11, "12": 12,
}

DAY_WORDS = {"послезавтра": 2, "завтра": 1, "сегодня": 0, "today": 0, "tomorrow": 1}


def _try_parse(text: str) -> Optional[datetime]:
    text = text.strip().lower()

    # "день месяц_рус год часы:минуты" / "день месяц_рус в часы часов"
    m = re.match(
        r"(\d{1,2})\s+([а-я]+)\s*(?:(\d{4}))?\s*(?:в\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:часов|ч|час)?\s*$",
        text,
    )
    if m:
        day, month_name, year, hour, minute = m.groups()
        month = MONTHS_RU.get(month_name)
        if not month:
            return None
        year = int(year) if year else datetime.now().year
        return datetime(year, month, int(day), int(hour), int(minute or 0))

    # "день месяц_рус год" (без времени)
    m = re.match(r"(\d{1,2})\s+([а-я]+)\s*(\d{4})?\s*$", text)
    if m:
        day, month_name, year = m.groups()
        month = MONTHS_RU.get(month_name)
        if not month:
            return None
        year = int(year) if year else datetime.now().year
        return datetime(year, month, int(day))

    # "дд мм в часы" (пробел вместо точки)
    m = re.match(
        r"(\d{1,2})\s+(\d{1,2})\s*(?:в\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:часов|ч|час)?\s*$",
        text,
    )
    if m:
        day, month, hour, minute = m.groups()
        now = datetime.now()
        year = now.year
        try:
            dt = datetime(int(year), int(month), int(day), int(hour), int(minute or 0))
            if dt < now:
                dt = dt.replace(year=year + 1)
            return dt
        except ValueError:
            pass

    # "дд.мм.гггг часы:минуты"
    m = re.match(
        r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*(?:в\s*)?(\d{1,2})(?::(\d{2}))?\s*$",
        text,
    )
    if m:
        day, month, year, hour, minute = m.groups()
        year = int(year) if year else datetime.now().year
        if year < 100:
            year += 2000
        return datetime(int(year), int(month), int(day), int(hour), int(minute or 0))

    # "дд.мм" (без года и времени)
    m = re.match(r"(\d{1,2})[./](\d{1,2})\s*$", text)
    if m:
        day, month = m.groups()
        now = datetime.now()
        year = now.year
        dt = datetime(int(year), int(month), int(day))
        if dt < now:
            dt = dt.replace(year=year + 1)
        return dt

    # "сегодня/завтра/послезавтра в часы:минуты"
    m = re.match(r"(сегодня|завтра|послезавтра|today|tomorrow)\s*(?:в\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:часов|ч|час)?\s*$", text)
    if m:
        day_word, hour, minute = m.groups()
        delta = DAY_WORDS[day_word]
        dt = datetime.now() + timedelta(days=delta)
        return dt.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)

    # "сегодня/завтра/послезавтра"
    if text in DAY_WORDS:
        delta = DAY_WORDS[text]
        dt = datetime.now() + timedelta(days=delta)
        return dt.replace(hour=18, minute=0, second=0, microsecond=0)

    # "в часы:минуты" (сегодня)
    m = re.match(r"(?:в\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:часов|ч|час)?\s*$", text)
    if m:
        hour, minute = m.groups()
        now = datetime.now()
        dt = now.replace(hour=int(hour), minute=int(minute or 0), second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    return None


def parse_deadline(text: str) -> Optional[str]:
    dt = _try_parse(text)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return None


def format_datetime_ru(dt_str: str) -> str:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return f"{dt.day} {months[dt.month - 1]} {dt.year} в {dt.hour}:{dt.minute:02d}"
