"""Дефолт 14:05 і дотягування цін на завтра, якщо oree спізнився.

ОРЕЕ публікує ціни на наступну добу після 14:00, тому запис без явно заданої
опції поводиться як 14:05, а якщо о цій годині цін ще немає — інтеграція
перепитує кожні 10 хвилин, але не пізніше 18:00.
"""

from datetime import datetime, timedelta
from pathlib import Path

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.oree_dam.api import KYIV, URL
from custom_components.oree_dam.const import (
    CONF_DAILY_REFRESH_AT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

FIX = Path(__file__).parent / "fixtures"

# Фікстура 11.2026 містить дні 01…12.11: 11-го ціни на завтра вже є,
# 12-го цін на завтра (13.11) немає — саме випадок «oree спізнився».
DAY_WITH_TOMORROW = datetime(2026, 11, 11, 14, 0, tzinfo=KYIV)
DAY_WITHOUT_TOMORROW = datetime(2026, 11, 12, 14, 0, tzinfo=KYIV)


async def setup_entry(hass, aioclient_mock, options=None) -> MockConfigEntry:
    aioclient_mock.post(URL, content=(FIX / "dv_11.2026.json").read_bytes())
    entry = MockConfigEntry(
        domain=DOMAIN, data={"market": "DAM"}, unique_id="DAM", options=options or {}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def fire_at(hass, freezer, moment: datetime) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.move_to(moment + timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_default_daily_time_is_1405(hass, aioclient_mock, freezer):
    """Запис без опції оновлюється о 14:05, хоч ключа в options немає."""
    await hass.config.async_set_time_zone("Europe/Kyiv")
    freezer.move_to(DAY_WITH_TOMORROW)
    entry = await setup_entry(hass, aioclient_mock, {CONF_UPDATE_INTERVAL: 1440})
    assert CONF_DAILY_REFRESH_AT not in entry.options

    before = aioclient_mock.call_count
    await fire_at(hass, freezer, DAY_WITH_TOMORROW.replace(minute=5, second=1))
    assert aioclient_mock.call_count > before, "дефолтний тригер 14:05 не спрацював"


async def test_cleared_time_beats_default(hass, aioclient_mock, freezer):
    """Явно очищене поле сильніше за дефолт: о 14:05 нічого не відбувається."""
    await hass.config.async_set_time_zone("Europe/Kyiv")
    freezer.move_to(DAY_WITH_TOMORROW)
    await setup_entry(
        hass, aioclient_mock, {CONF_UPDATE_INTERVAL: 1440, CONF_DAILY_REFRESH_AT: ""}
    )

    before = aioclient_mock.call_count
    await fire_at(hass, freezer, DAY_WITH_TOMORROW.replace(minute=5, second=1))
    assert aioclient_mock.call_count == before


async def test_options_flow_suggests_1405_for_new_entry(hass, aioclient_mock):
    """Нова форма опцій пропонує 14:05, уже збережене значення не переписує."""
    entry = await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {
        key.schema: key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested[CONF_DAILY_REFRESH_AT] == "14:05"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 30, CONF_DAILY_REFRESH_AT: "15:00:00"}
    )
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {
        key.schema: key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested[CONF_DAILY_REFRESH_AT] == "15:00:00"


async def test_catch_up_retries_while_tomorrow_missing(hass, aioclient_mock, freezer):
    """Цін на завтра немає — перепитуємо кожні 10 хвилин."""
    await hass.config.async_set_time_zone("Europe/Kyiv")
    freezer.move_to(DAY_WITHOUT_TOMORROW)
    await setup_entry(hass, aioclient_mock, {CONF_UPDATE_INTERVAL: 1440})

    await fire_at(hass, freezer, DAY_WITHOUT_TOMORROW.replace(minute=5, second=1))
    after_trigger = aioclient_mock.call_count

    await fire_at(hass, freezer, DAY_WITHOUT_TOMORROW.replace(minute=16, second=1))
    after_first_retry = aioclient_mock.call_count
    assert after_first_retry > after_trigger, "перший ретрай не відбувся"

    await fire_at(hass, freezer, DAY_WITHOUT_TOMORROW.replace(minute=26, second=1))
    assert aioclient_mock.call_count > after_first_retry, "другий ретрай не відбувся"


async def test_catch_up_stops_when_tomorrow_arrives(hass, aioclient_mock, freezer):
    """Ціни на завтра вже є — дотягувати нічого, ретраїв немає."""
    await hass.config.async_set_time_zone("Europe/Kyiv")
    freezer.move_to(DAY_WITH_TOMORROW)
    entry = await setup_entry(hass, aioclient_mock, {CONF_UPDATE_INTERVAL: 1440})
    assert entry.runtime_data.data.slots_for(DAY_WITH_TOMORROW.date() + timedelta(days=1))

    await fire_at(hass, freezer, DAY_WITH_TOMORROW.replace(minute=5, second=1))
    after_trigger = aioclient_mock.call_count

    await fire_at(hass, freezer, DAY_WITH_TOMORROW.replace(minute=16, second=1))
    await fire_at(hass, freezer, DAY_WITH_TOMORROW.replace(minute=26, second=1))
    assert aioclient_mock.call_count == after_trigger, "зайві запити при наявних цінах"


async def test_catch_up_stops_at_18(hass, aioclient_mock, freezer):
    """Після 18:00 дотягування припиняється, далі — звичайний інтервал."""
    await hass.config.async_set_time_zone("Europe/Kyiv")
    start = DAY_WITHOUT_TOMORROW.replace(hour=17, minute=40)
    freezer.move_to(start)
    await setup_entry(
        hass,
        aioclient_mock,
        {CONF_UPDATE_INTERVAL: 1440, CONF_DAILY_REFRESH_AT: "17:45"},
    )

    await fire_at(hass, freezer, start.replace(minute=45, second=1))
    await fire_at(hass, freezer, start.replace(minute=56, second=1))
    before_cutoff = aioclient_mock.call_count

    # 18:06 — за межею вікна дотягування
    await fire_at(hass, freezer, start.replace(hour=18, minute=6, second=1))
    await fire_at(hass, freezer, start.replace(hour=18, minute=16, second=1))
    assert aioclient_mock.call_count == before_cutoff, "ретраї після 18:00"
