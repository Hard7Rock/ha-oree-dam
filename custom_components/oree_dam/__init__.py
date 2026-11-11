"""ОРЕЕ: погодинні ціни РДН/ВДР з oree.com.ua."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_DAILY_REFRESH_AT
from .coordinator import OreeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type OreeConfigEntry = ConfigEntry[OreeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: OreeConfigEntry) -> bool:
    """Налаштувати запис: підняти кеш, перше опитування, сенсори."""
    coordinator = OreeCoordinator(hass, entry)
    await coordinator.async_load_cache()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_setup_daily_refresh(hass, entry, coordinator)
    # Зміна опцій → перезавантаження запису, щоб підхопити новий інтервал і час.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OreeConfigEntry) -> bool:
    """Вивантажити запис."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: OreeConfigEntry) -> None:
    """Застосувати нові опції без рестарту HA."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_setup_daily_refresh(
    hass: HomeAssistant, entry: OreeConfigEntry, coordinator: OreeCoordinator
) -> None:
    """Повісити щоденне примусове оновлення на заданий час, якщо його задано."""
    raw = entry.options.get(CONF_DAILY_REFRESH_AT)
    if not raw:
        return

    moment = dt_util.parse_time(raw)
    if moment is None:
        _LOGGER.warning("Не розумію час щоденного оновлення %r, ігнорую", raw)
        return

    async def _handle_daily(now) -> None:
        _LOGGER.debug("Щоденне оновлення цін о %s", raw)
        await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _handle_daily,
            hour=moment.hour,
            minute=moment.minute,
            second=moment.second,
        )
    )
