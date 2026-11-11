"""ОРЕЕ: погодинні ціни РДН/ВДР з oree.com.ua."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import KYIV
from .const import (
    CATCH_UP_EVERY_MIN,
    CATCH_UP_UNTIL_HOUR,
    CONF_DAILY_REFRESH_AT,
    DEFAULT_DAILY_REFRESH_AT,
)
from .coordinator import OreeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]

type OreeConfigEntry = ConfigEntry[OreeCoordinator]


def daily_refresh_time(entry: ConfigEntry) -> time | None:
    """Час щоденного оновлення: None — користувач свідомо його вимкнув.

    Ключа немає в options (запис ніколи не налаштовували) — діє дефолт 14:05.
    Ключ є, але порожній — тригера немає.
    """
    raw = entry.options.get(CONF_DAILY_REFRESH_AT, DEFAULT_DAILY_REFRESH_AT)
    if not raw:
        return None
    moment = dt_util.parse_time(raw)
    if moment is None:
        _LOGGER.warning("Не розумію час щоденного оновлення %r, ігнорую", raw)
    return moment


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
    moment = daily_refresh_time(entry)
    if moment is None:
        return

    catch_up = TomorrowCatchUp(hass, entry, coordinator)
    entry.async_on_unload(
        async_track_time_change(
            hass,
            catch_up.async_daily_refresh,
            hour=moment.hour,
            minute=moment.minute,
            second=moment.second,
        )
    )


class TomorrowCatchUp:
    """Дотягує ціни на завтра, якщо oree спізнився з публікацією.

    Після щоденного тригера перепитує кожні CATCH_UP_EVERY_MIN хвилин, поки
    завтрашні ціни не зʼявляться, але не пізніше CATCH_UP_UNTIL_HOUR. Таймер
    один, паралельних запитів немає.
    """

    def __init__(
        self, hass: HomeAssistant, entry: OreeConfigEntry, coordinator: OreeCoordinator
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._unsub = None
        self._busy = False
        entry.async_on_unload(self._async_stop)

    async def async_daily_refresh(self, _now: datetime) -> None:
        """Щоденний тригер: оновитись і, якщо треба, почати дотягування."""
        _LOGGER.debug("Щоденне оновлення цін")
        await self._coordinator.async_request_refresh()
        self._async_start()

    @callback
    def _async_start(self) -> None:
        if self._unsub is None:
            self._unsub = async_track_time_interval(
                self._hass, self._async_retry, timedelta(minutes=CATCH_UP_EVERY_MIN)
            )

    @callback
    def _async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _has_tomorrow(self) -> bool:
        data = self._coordinator.data
        tomorrow = dt_util.now(KYIV).date() + timedelta(days=1)
        return bool(data and data.slots_for(tomorrow))

    async def _async_retry(self, _now: datetime) -> None:
        if self._busy:
            return
        if self._has_tomorrow() or dt_util.now().hour >= CATCH_UP_UNTIL_HOUR:
            self._async_stop()
            return
        self._busy = True
        try:
            _LOGGER.debug("Цін на завтра ще немає, перепитую oree")
            await self._coordinator.async_refresh()
        finally:
            self._busy = False
        if self._has_tomorrow():
            self._async_stop()
