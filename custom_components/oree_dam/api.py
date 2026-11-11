"""Отримання та розбір погодинних цін РДН з oree.com.ua.

Модуль навмисно не залежить від Home Assistant: його можна тестувати окремо
і використовувати з будь-якого скрипта.

Джерело: POST https://www.oree.com.ua/index.php/pricectr/data_view
  поля форми: date=MM.YYYY, market=DAM|IDM, zone=IPS
  відповідь:  JSON {"caption": "...", "content": "<table>...</table>"}
  таблиця:    рядок на добу, колонка «Дата» + години 1..24 (або 1..25),
              ціни в грн/МВт·год.

Перехід на літній/зимовий час oree кодує так:
  * 23-годинна доба — у рядку 24 колонки, остання порожня;
  * 25-годинна доба — у місяці 25 колонок, у цей день заповнена й 25-та;
  * у звичайні дні місяця з 25 колонками 25-та порожня.
Тобто непорожні значення йдуть підряд у реальному часі, і їх кількість
має дорівнювати кількості годин у добі за київським часом. Якщо не дорівнює —
дані вважаються пошкодженими (ми не вгадуємо, яку годину пропущено).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import html
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
URL = "https://www.oree.com.ua/index.php/pricectr/data_view"
REFERER = "https://www.oree.com.ua/index.php/pricectr"
MARKETS = ("DAM", "IDM")

# Діапазон, поза яким значення вважаємо помилкою джерела, а не ціною.
# Регуляторні price caps РДН — у межах кількох десятків тисяч грн/МВт·год.
PRICE_MIN_MWH = 0.0
PRICE_MAX_MWH = 100_000.0

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


class OreeError(Exception):
    """Базова помилка джерела цін."""


class OreeFormatError(OreeError):
    """Відповідь oree не відповідає очікуваному формату."""


@dataclass(frozen=True)
class PriceSlot:
    """Одна година з ціною. Час — у UTC, ціна — грн/МВт·год."""

    start: datetime
    end: datetime
    price_mwh: float

    @property
    def price_kwh(self) -> float:
        """Ціна в грн/кВт·год."""
        return self.price_mwh / 1000.0

    def as_dict(self) -> dict[str, Any]:
        """Подання для атрибутів сенсора (локальний час, грн/кВт·год)."""
        return {
            "start": self.start.astimezone(KYIV).isoformat(),
            "end": self.end.astimezone(KYIV).isoformat(),
            "price": round(self.price_kwh, 5),
        }


def month_key(day: date) -> str:
    """Рядок місяця у форматі, який чекає oree: MM.YYYY."""
    return f"{day.month:02d}.{day.year}"


def form_data(month: str, market: str = "DAM") -> dict[str, str]:
    """Поля POST-запиту. Після 23.02.2022 зона лише одна — IPS."""
    if market not in MARKETS:
        raise ValueError(f"невідомий ринок: {market}")
    return {"date": month, "market": market, "zone": "IPS"}


def decode_response(raw: bytes) -> dict[str, Any]:
    """Розібрати тіло відповіді. Сервер оголошує windows-1251, тож пробуємо обидва."""
    for enc in ("utf-8", "cp1251"):
        try:
            data = json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "content" in data:
            return data
        raise OreeFormatError("у відповіді немає поля content")
    raise OreeFormatError("відповідь не є JSON")


def _cell_text(cell: str) -> str:
    return html.unescape(_TAG_RE.sub("", cell)).replace("\xa0", " ").strip()


def _to_float(text: str) -> float | None:
    text = text.replace(" ", "").replace(",", ".")
    if text in ("", "-", "—"):
        return None
    try:
        return float(text)
    except ValueError as err:
        raise OreeFormatError(f"нечислове значення ціни: {text!r}") from err


def parse_month(content_html: str) -> dict[date, list[float | None]]:
    """Таблиця місяця → {дата: [значення по колонках годин]}."""
    days: dict[date, list[float | None]] = {}
    header_len: int | None = None
    for row in _ROW_RE.findall(content_html):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row)]
        if not cells:
            continue
        if cells[0] == "Дата" or cells[0].lower() == "date":
            header_len = len(cells) - 1
            continue
        m = _DATE_RE.match(cells[0])
        if not m:
            continue
        values = [_to_float(c) for c in cells[1:]]
        if header_len is not None and len(values) != header_len:
            raise OreeFormatError(
                f"{cells[0]}: {len(values)} колонок замість {header_len}"
            )
        day = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        days[day] = values
    if not days:
        raise OreeFormatError("у таблиці не знайдено жодного дня")
    return days


def hours_in_day(day: date, tz: ZoneInfo = KYIV) -> list[datetime]:
    """Початки годин доби в UTC — 23, 24 або 25 штук залежно від переходу часу."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(timezone.utc)
    out: list[datetime] = []
    cur = start
    while cur < end:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def build_slots(day: date, values: list[float | None], tz: ZoneInfo = KYIV) -> list[PriceSlot]:
    """Значення одного дня → години з UTC-часом. Порожні колонки пропускаються."""
    prices = [v for v in values if v is not None]
    if not prices:
        return []  # день ще не опублікований
    starts = hours_in_day(day, tz)
    if len(prices) != len(starts):
        raise OreeFormatError(
            f"{day.isoformat()}: {len(prices)} цін на {len(starts)} годин доби — "
            "не зіставляється з переходом часу, дані не приймаються"
        )
    for p in prices:
        if not PRICE_MIN_MWH <= p <= PRICE_MAX_MWH:
            raise OreeFormatError(f"{day.isoformat()}: ціна поза межами: {p}")
    return [
        PriceSlot(start=s, end=s + timedelta(hours=1), price_mwh=p)
        for s, p in zip(starts, prices)
    ]


def parse_response(raw: bytes) -> dict[date, list[PriceSlot]]:
    """Сира відповідь oree → {дата: години}. Дні без даних не повертаються."""
    data = decode_response(raw)
    out: dict[date, list[PriceSlot]] = {}
    for day, values in parse_month(data["content"]).items():
        slots = build_slots(day, values)
        if slots:
            out[day] = slots
    return out


def months_needed(now_local: datetime) -> list[str]:
    """Які місяці тягнути, щоб мати сьогодні й завтра (на межі місяця — два)."""
    today = now_local.date()
    tomorrow = today + timedelta(days=1)
    months = [month_key(today)]
    if month_key(tomorrow) not in months:
        months.append(month_key(tomorrow))
    return months
