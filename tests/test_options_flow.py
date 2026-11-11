"""Тести опцій запису: інтервал опитування та щоденне примусове оновлення."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.oree_dam.api import KYIV, URL
from custom_components.oree_dam.const import (
    CONF_DAILY_REFRESH_AT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_DAILY_REFRESH_AT,
    DOMAIN,
)

FIX = Path(__file__).parent / "fixtures"


def fixture(month: str = "11.2026") -> bytes:
    return (FIX / f"dv_{month}.json").read_bytes()


async def setup_entry(hass, aioclient_mock, options: dict | None = None) -> MockConfigEntry:
    aioclient_mock.post(URL, content=fixture())
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"market": "DAM"},
        unique_id="DAM",
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def use_kyiv_timezone(hass) -> None:
    """Тригер часу рахується в локальній TZ Home Assistant."""
    await hass.config.async_set_time_zone("Europe/Kyiv")


async def fire_at(hass, freezer, moment: datetime) -> None:
    """Перевести годинник і дати координатору відпрацювати (з урахуванням debouncer)."""
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.move_to(moment + timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_default_interval_is_30_minutes(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    assert entry.runtime_data.update_interval == timedelta(minutes=30)


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_options_flow_changes_update_interval(hass, aioclient_mock):
    """Нове значення інтервалу застосовується без рестарту HA."""
    entry = await setup_entry(hass, aioclient_mock)
    assert entry.runtime_data.update_interval == timedelta(minutes=30)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 120}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.options[CONF_UPDATE_INTERVAL] == 120
    # запис перезавантажено update-listener'ом, координатор уже з новим інтервалом
    assert entry.runtime_data.update_interval == timedelta(minutes=120)


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_options_flow_stores_cleared_time_as_empty(hass, aioclient_mock):
    """Очищене поле зберігається як "" — інакше знову підхопився б дефолт 14:05."""
    entry = await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 60}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options[CONF_DAILY_REFRESH_AT] == ""


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_options_flow_stores_daily_time(hass, aioclient_mock):
    entry = await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_UPDATE_INTERVAL: 45, CONF_DAILY_REFRESH_AT: "14:30:00"},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options[CONF_DAILY_REFRESH_AT] == "14:30:00"
    assert entry.runtime_data.update_interval == timedelta(minutes=45)


async def test_daily_time_forces_refresh(hass, aioclient_mock, freezer):
    """О заданій годині ціни тягнуться навіть поза інтервалом опитування."""
    await use_kyiv_timezone(hass)
    freezer.move_to(datetime(2026, 11, 11, 14, 0, tzinfo=KYIV))
    entry = await setup_entry(
        hass,
        aioclient_mock,
        {CONF_UPDATE_INTERVAL: 1440, CONF_DAILY_REFRESH_AT: "14:30:00"},
    )
    assert entry.runtime_data.update_interval == timedelta(minutes=1440)

    before = aioclient_mock.call_count
    await fire_at(hass, freezer, datetime(2026, 11, 11, 14, 30, 1, tzinfo=KYIV))
    assert aioclient_mock.call_count > before, "щоденний тригер не смикнув оновлення"


async def test_no_daily_time_means_no_trigger(hass, aioclient_mock, freezer):
    """Свідомо очищений час — о 14:05 і 14:30 нічого не відбувається."""
    await use_kyiv_timezone(hass)
    freezer.move_to(datetime(2026, 11, 11, 14, 0, tzinfo=KYIV))
    entry = await setup_entry(
        hass, aioclient_mock, {CONF_UPDATE_INTERVAL: 1440, CONF_DAILY_REFRESH_AT: ""}
    )
    assert entry.options[CONF_DAILY_REFRESH_AT] == ""

    before = aioclient_mock.call_count
    await fire_at(hass, freezer, datetime(2026, 11, 11, 14, 5, 1, tzinfo=KYIV))
    await fire_at(hass, freezer, datetime(2026, 11, 11, 14, 30, 1, tzinfo=KYIV))
    assert aioclient_mock.call_count == before, "оновлення без заданого часу"


async def test_daily_trigger_removed_after_unload(hass, aioclient_mock, freezer):
    """Після вивантаження запису тригер знято."""
    await use_kyiv_timezone(hass)
    freezer.move_to(datetime(2026, 11, 11, 14, 0, tzinfo=KYIV))
    entry = await setup_entry(
        hass,
        aioclient_mock,
        {CONF_UPDATE_INTERVAL: 1440, CONF_DAILY_REFRESH_AT: "14:30:00"},
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    before = aioclient_mock.call_count
    await fire_at(hass, freezer, datetime(2026, 11, 11, 14, 30, 1, tzinfo=KYIV))
    assert aioclient_mock.call_count == before
