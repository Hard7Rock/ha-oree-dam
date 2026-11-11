"""Інтеграційні тести: компонент у справжньому Home Assistant.

oree підмінено записаними відповідями з tests/fixtures, час заморожено.
"""

from datetime import datetime
from pathlib import Path

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.oree_dam.api import KYIV, URL
from custom_components.oree_dam.const import DOMAIN

FIX = Path(__file__).parent / "fixtures"


def fixture(month: str) -> bytes:
    return (FIX / f"dv_{month}.json").read_bytes()


def entity(hass: HomeAssistant, entry: MockConfigEntry, key: str):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, f"немає сутності {key}"
    return hass.states.get(eid)


async def setup(hass, aioclient_mock, month="11.2026") -> MockConfigEntry:
    aioclient_mock.post(URL, content=fixture(month))
    entry = MockConfigEntry(domain=DOMAIN, data={"market": "DAM"}, unique_id="DAM")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_current_price_matches_oree_table(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED

    cur = entity(hass, entry, "current_price")
    # 15:30 — година 16 у таблиці oree (15:00–16:00)
    today = cur.attributes["today"]
    assert len(today) == 24
    assert today[15]["start"].startswith("2026-11-11T15:00:00+02:00")
    assert float(cur.state) == pytest.approx(today[15]["price"])
    assert cur.attributes["unit_of_measurement"] == "UAH/kWh"

    # 12.11 на oree вже опубліковано
    assert cur.attributes["tomorrow_valid"] is True
    assert len(cur.attributes["tomorrow"]) == 24
    assert entity(hass, entry, "tomorrow_average").state not in ("unknown", "unavailable")

    nxt = entity(hass, entry, "next_hour_price")
    assert float(nxt.state) == pytest.approx(today[16]["price"])

    prices = [h["price"] for h in today]
    assert float(entity(hass, entry, "today_min").state) == pytest.approx(min(prices))
    assert float(entity(hass, entry, "today_max").state) == pytest.approx(max(prices))

    rank = entity(hass, entry, "current_hour_rank")
    assert 1 <= int(rank.state) <= 24
    assert rank.attributes["hours_in_day"] == 24
    assert len(rank.attributes["cheapest_hours"]) == 6


@pytest.mark.freeze_time(datetime(2026, 10, 25, 12, 0, tzinfo=KYIV))
async def test_fall_back_day_has_25_hours(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, "10.2026")
    rank = entity(hass, entry, "current_hour_rank")
    assert rank.attributes["hours_in_day"] == 25
    assert len(entity(hass, entry, "current_price").attributes["today"]) == 25


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_oree_outage_keeps_cached_prices(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    before = entity(hass, entry, "current_price").state

    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, exc=aiohttp.ClientError("oree лежить"))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    cur = entity(hass, entry, "current_price")
    assert cur.state == before  # ціна не зникла
    assert "oree лежить" in cur.attributes["last_error"]


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_no_data_and_no_cache_retries_setup(hass, aioclient_mock):
    aioclient_mock.post(URL, exc=aiohttp.ClientError("oree лежить"))
    entry = MockConfigEntry(domain=DOMAIN, data={"market": "DAM"}, unique_id="DAM")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_cache_survives_restart_when_oree_down(hass, aioclient_mock, hass_storage):
    # 1-й запуск: oree працює — кеш записано на диск
    entry = await setup(hass, aioclient_mock)
    price = entity(hass, entry, "current_price").state
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

    # 2-й запуск: oree лежить — підіймаємось з кешу
    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, exc=aiohttp.ClientError("oree лежить"))
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entity(hass, entry, "current_price").state == price


async def test_config_flow_one_entry_per_market(hass, aioclient_mock):
    aioclient_mock.post(URL, content=fixture("11.2026"))
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"market": "DAM"})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()

    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"market": "DAM"})
    assert r["type"] == "abort" and r["reason"] == "already_configured"
