"""Сенсори цін РДН."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import OreeConfigEntry
from .api import KYIV, PriceSlot
from .const import DOMAIN, MARKET_NAMES, UNIT
from .coordinator import OreeCoordinator, PriceData

# Скільки годин віддавати в атрибутах «найдешевші/найдорожчі» — під правила фази 1.
TOP_N = 6


def _today(data: PriceData) -> list[PriceSlot]:
    return data.slots_for(dt_util.now(KYIV).date())


def _tomorrow(data: PriceData) -> list[PriceSlot]:
    return data.slots_for(dt_util.now(KYIV).date() + timedelta(days=1))


def _current(data: PriceData) -> float | None:
    s = data.slot_at(dt_util.utcnow())
    return s.price_kwh if s else None


def _next_hour(data: PriceData) -> float | None:
    s = data.slot_at(dt_util.utcnow() + timedelta(hours=1))
    return s.price_kwh if s else None


def _agg(fn: Callable[[list[float]], float], slots_fn: Callable[[PriceData], list[PriceSlot]]):
    def inner(data: PriceData) -> float | None:
        prices = [s.price_kwh for s in slots_fn(data)]
        return fn(prices) if prices else None

    return inner


def _rank(data: PriceData) -> int | None:
    """Місце поточної години серед годин доби: 1 — найдешевша."""
    cur = data.slot_at(dt_util.utcnow())
    today = _today(data)
    if not cur or not today:
        return None
    ordered = sorted(today, key=lambda s: (s.price_mwh, s.start))
    return next(i for i, s in enumerate(ordered, 1) if s.start == cur.start)


def _local(s: PriceSlot) -> str:
    return s.start.astimezone(KYIV).strftime("%H:%M")


@dataclass(frozen=True, kw_only=True)
class OreeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PriceData], float | int | None]
    attrs_fn: Callable[[PriceData], dict[str, Any]] | None = None


def _price_attrs(data: PriceData) -> dict[str, Any]:
    """Повні ряди на сьогодні/завтра — для графіків і оптимізатора."""
    today, tomorrow = _today(data), _tomorrow(data)
    return {
        "today": [s.as_dict() for s in today],
        "tomorrow": [s.as_dict() for s in tomorrow],
        "tomorrow_valid": bool(tomorrow),
        "source": "oree.com.ua, грн/МВт·год ÷ 1000, без ПДВ і тарифів на передачу/розподіл",
        "last_success": data.last_success.isoformat() if data.last_success else None,
        "last_error": data.last_error,
    }


def _rank_attrs(data: PriceData) -> dict[str, Any]:
    today = sorted(_today(data), key=lambda s: (s.price_mwh, s.start))
    return {
        "hours_in_day": len(today),
        "cheapest_hours": [_local(s) for s in today[:TOP_N]],
        "most_expensive_hours": [_local(s) for s in today[-TOP_N:][::-1]],
    }


SENSORS: tuple[OreeSensorDescription, ...] = (
    OreeSensorDescription(
        key="current_price",
        translation_key="current_price",
        native_unit_of_measurement=UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=_current,
        attrs_fn=_price_attrs,
    ),
    OreeSensorDescription(
        key="next_hour_price",
        translation_key="next_hour_price",
        native_unit_of_measurement=UNIT,
        suggested_display_precision=3,
        value_fn=_next_hour,
    ),
    OreeSensorDescription(
        key="today_min",
        translation_key="today_min",
        native_unit_of_measurement=UNIT,
        suggested_display_precision=3,
        value_fn=_agg(min, _today),
    ),
    OreeSensorDescription(
        key="today_max",
        translation_key="today_max",
        native_unit_of_measurement=UNIT,
        suggested_display_precision=3,
        value_fn=_agg(max, _today),
    ),
    OreeSensorDescription(
        key="today_average",
        translation_key="today_average",
        native_unit_of_measurement=UNIT,
        suggested_display_precision=3,
        value_fn=_agg(mean, _today),
    ),
    OreeSensorDescription(
        key="tomorrow_average",
        translation_key="tomorrow_average",
        native_unit_of_measurement=UNIT,
        suggested_display_precision=3,
        value_fn=_agg(mean, _tomorrow),
    ),
    OreeSensorDescription(
        key="current_hour_rank",
        translation_key="current_hour_rank",
        value_fn=_rank,
        attrs_fn=_rank_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: OreeConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(OreeSensor(coordinator, entry, d) for d in SENSORS)

    @callback
    def _hour_tick(_now: datetime) -> None:
        # Нова година — перерахувати стан без запиту до oree.
        coordinator.async_update_listeners()

    entry.async_on_unload(async_track_time_change(hass, _hour_tick, minute=0, second=1))


class OreeSensor(CoordinatorEntity[OreeCoordinator], SensorEntity):
    """Сенсор ціни."""

    _attr_has_entity_name = True
    entity_description: OreeSensorDescription

    def __init__(self, coordinator: OreeCoordinator, entry: OreeConfigEntry, desc: OreeSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ОРЕЕ {MARKET_NAMES[coordinator.market]}",
            manufacturer="ДП «Оператор ринку»",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.oree.com.ua/index.php/pricectr",
        )

    @property
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
