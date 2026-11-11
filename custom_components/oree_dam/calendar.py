"""Календар цін: одна подія на кожну годину доби.

Призначення — машиночитане джерело для автоматизацій: через стандартний
`calendar.get_events` можна одним викликом дістати всі години проміжку разом
із ціною, рангом і перцентилем, і вже в шаблоні вибрати вікно для заряду АКБ.

Календар лише для читання: події створюються з кешу координатора, жодних
додаткових запитів до oree.
"""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import OreeConfigEntry
from .api import KYIV, PriceSlot
from .const import DOMAIN, MARKET_NAMES
from .coordinator import OreeCoordinator, PriceData


def _ranks(slots: list[PriceSlot]) -> dict[datetime, int]:
    """Місце кожної години в добі: 1 — найдешевша."""
    ordered = sorted(slots, key=lambda s: (s.price_mwh, s.start))
    return {s.start: i for i, s in enumerate(ordered, 1)}


def _percentile(rank: int, hours_in_day: int) -> int:
    """0 — найдешевша година доби, 100 — найдорожча."""
    if hours_in_day < 2:
        return 0
    return round((rank - 1) / (hours_in_day - 1) * 100)


def describe(slot: PriceSlot, rank: int, hours_in_day: int, market: str) -> str:
    """Машиночитаний опис: по рядку `ключ: значення`, без регулярок на боці HA."""
    return "\n".join(
        (
            f"price_kwh: {slot.price_kwh:.4f}",
            f"price_mwh: {slot.price_mwh:.2f}",
            f"rank: {rank}",
            f"hours_in_day: {hours_in_day}",
            f"percentile: {_percentile(rank, hours_in_day)}",
            f"market: {market}",
        )
    )


def summarize(slot: PriceSlot, rank: int, hours_in_day: int) -> str:
    """Людиночитаний заголовок події."""
    return f"{slot.price_kwh:.2f} грн/кВт·год · ранг {rank}/{hours_in_day}"


def day_events(data: PriceData, day: date, market: str, entry_id: str) -> list[CalendarEvent]:
    """Події однієї доби. 23 або 25 годин у дні переходу часу — як у даних."""
    slots = data.slots_for(day)
    if not slots:
        return []

    hours_in_day = len(slots)
    ranks = _ranks(slots)
    events: list[CalendarEvent] = []
    for slot in sorted(slots, key=lambda s: s.start):
        rank = ranks[slot.start]
        start = slot.start.astimezone(KYIV)
        events.append(
            CalendarEvent(
                start=start,
                end=slot.end.astimezone(KYIV),
                summary=summarize(slot, rank, hours_in_day),
                description=describe(slot, rank, hours_in_day, market),
                uid=f"{entry_id}_{start.isoformat()}",
            )
        )
    return events


async def async_setup_entry(
    hass: HomeAssistant, entry: OreeConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([OreePriceCalendar(coordinator, entry)])


class OreePriceCalendar(CoordinatorEntity[OreeCoordinator], CalendarEntity):
    """Ціни як календар: подія = година."""

    _attr_has_entity_name = True
    _attr_translation_key = "prices"

    def __init__(self, coordinator: OreeCoordinator, entry: OreeConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_prices"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ОРЕЕ {MARKET_NAMES[coordinator.market]}",
            manufacturer="ДП «Оператор ринку»",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.oree.com.ua/index.php/pricectr",
        )

    def _all_events(self) -> list[CalendarEvent]:
        """Усі години, що є в кеші (вчора/сьогодні/завтра)."""
        data = self.coordinator.data
        events: list[CalendarEvent] = []
        for day in sorted(data.days):
            events.extend(day_events(data, day, self.coordinator.market, self._entry_id))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """Поточна година — від неї залежить стан on/off."""
        now = dt_util.now(KYIV)
        for ev in self._all_events():
            if ev.start <= now < ev.end:
                return ev
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Усі години, що перетинаються з проміжком."""
        return [
            ev for ev in self._all_events() if ev.start < end_date and ev.end > start_date
        ]
