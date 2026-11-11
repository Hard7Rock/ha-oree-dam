"""Координатор: тягне ціни з oree, тримає кеш на диску, віддає сенсорам."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import api
from .const import (
    CONF_MARKET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_MARKET,
    DOMAIN,
    KEEP_PAST_DAYS,
    REQUEST_TIMEOUT_S,
    STORAGE_VERSION,
    UPDATE_INTERVAL_MIN,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PriceData:
    """Стан, який бачать сенсори."""

    days: dict[date, list[api.PriceSlot]] = field(default_factory=dict)
    last_success: datetime | None = None
    last_error: str | None = None

    def slots_for(self, day: date) -> list[api.PriceSlot]:
        return self.days.get(day, [])

    def slot_at(self, moment: datetime) -> api.PriceSlot | None:
        moment = moment.astimezone(timezone.utc)
        for slots in self.days.values():
            for s in slots:
                if s.start <= moment < s.end:
                    return s
        return None


class OreeCoordinator(DataUpdateCoordinator[PriceData]):
    """Опитує oree раз на заданий в опціях інтервал (дефолт UPDATE_INTERVAL_MIN)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval_min = int(entry.options.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_MIN))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data.get(CONF_MARKET, DEFAULT_MARKET)}",
            update_interval=timedelta(minutes=interval_min),
        )
        self.market: str = entry.data.get(CONF_MARKET, DEFAULT_MARKET)
        self._session = async_get_clientsession(hass)
        self._store: Store[dict] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._cache = PriceData()

    # ── кеш на диску ─────────────────────────────────────────────────────
    async def async_load_cache(self) -> None:
        """Підняти кеш після рестарту HA, щоб не сліпнути, якщо oree недоступний."""
        stored = await self._store.async_load()
        if not stored:
            return
        try:
            for iso_day, rows in stored.get("days", {}).items():
                self._cache.days[date.fromisoformat(iso_day)] = [
                    api.PriceSlot(
                        start=datetime.fromisoformat(s),
                        end=datetime.fromisoformat(s) + timedelta(hours=1),
                        price_mwh=float(p),
                    )
                    for s, p in rows
                ]
            if stored.get("last_success"):
                self._cache.last_success = datetime.fromisoformat(stored["last_success"])
        except (ValueError, TypeError, KeyError) as err:
            _LOGGER.warning("Кеш цін пошкоджено, ігнорую: %s", err)
            self._cache = PriceData()

    async def _async_save_cache(self) -> None:
        await self._store.async_save(
            {
                "days": {
                    d.isoformat(): [[s.start.isoformat(), s.price_mwh] for s in slots]
                    for d, slots in self._cache.days.items()
                },
                "last_success": (
                    self._cache.last_success.isoformat() if self._cache.last_success else None
                ),
            }
        )

    # ── опитування ───────────────────────────────────────────────────────
    async def _fetch_month(self, month: str) -> dict[date, list[api.PriceSlot]]:
        async with asyncio.timeout(REQUEST_TIMEOUT_S):
            async with self._session.post(
                api.URL,
                data=api.form_data(month, self.market),
                headers={
                    "User-Agent": USER_AGENT,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": api.REFERER,
                },
            ) as resp:
                if resp.status != 200:
                    raise api.OreeError(f"HTTP {resp.status} для {month}")
                raw = await resp.read()
        return api.parse_response(raw)

    async def _async_update_data(self) -> PriceData:
        now_local = dt_util.now(api.KYIV)
        today = now_local.date()
        try:
            for month in api.months_needed(now_local):
                self._cache.days.update(await self._fetch_month(month))
        except (api.OreeError, aiohttp.ClientError, TimeoutError) as err:
            self._cache.last_error = f"{type(err).__name__}: {err}"
            if self._cache.slots_for(today):
                # Сьогоднішні ціни є з кешу — не ламаємо сенсори, лише попереджаємо.
                _LOGGER.warning("oree недоступний, працюю з кешу: %s", err)
                return self._cache
            raise UpdateFailed(f"Немає цін на сьогодні: {err}") from err

        cutoff = today - timedelta(days=KEEP_PAST_DAYS)
        self._cache.days = {d: s for d, s in self._cache.days.items() if d >= cutoff}
        self._cache.last_success = dt_util.utcnow()
        self._cache.last_error = None
        await self._async_save_cache()
        return self._cache
