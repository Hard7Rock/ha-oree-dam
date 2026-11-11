"""Тести календаря цін: подія на кожну годину, доступна через calendar.get_events."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.oree_dam.api import KYIV, URL
from custom_components.oree_dam.calendar import day_events
from custom_components.oree_dam.const import DOMAIN

FIX = Path(__file__).parent / "fixtures"


def fixture(month: str) -> bytes:
    return (FIX / f"dv_{month}.json").read_bytes()


async def setup(hass, aioclient_mock, month="11.2026") -> MockConfigEntry:
    aioclient_mock.post(URL, content=fixture(month))
    entry = MockConfigEntry(domain=DOMAIN, data={"market": "DAM"}, unique_id="DAM")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def calendar_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("calendar", DOMAIN, f"{entry.entry_id}_prices")
    assert eid, "календар не створено"
    return eid


def sensor_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, f"немає сутності {key}"
    return eid


async def get_events(hass, eid: str, start: datetime, end: datetime) -> list[dict]:
    """Виклик штатного сервісу, як це робитиме автоматизація користувача."""
    resp = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": eid, "start_date_time": start, "end_date_time": end},
        blocking=True,
        return_response=True,
    )
    return resp[eid]["events"]


def parse_description(text: str) -> dict[str, str]:
    """`ключ: значення` по рядку — саме так це розбиратиме шаблон HA."""
    out = {}
    for line in text.splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    return out


def day_range(day: datetime) -> tuple[datetime, datetime]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_day_has_one_event_per_hour(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    start, end = day_range(datetime(2026, 11, 11, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)

    assert len(events) == 24
    starts = [datetime.fromisoformat(e["start"]) for e in events]
    assert starts == sorted(starts)
    assert starts[0] == datetime(2026, 11, 11, 0, 0, tzinfo=KYIV)
    for i, ev in enumerate(events):
        s = datetime.fromisoformat(ev["start"])
        e = datetime.fromisoformat(ev["end"])
        assert e - s == timedelta(hours=1)
        assert s == starts[0] + timedelta(hours=i)


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_event_prices_match_sensor_series(hass, aioclient_mock):
    """Ціни в календарі — ті самі, що в атрибуті today сенсора."""
    entry = await setup(hass, aioclient_mock)
    today = hass.states.get(sensor_id(hass, entry, "current_price")).attributes["today"]

    start, end = day_range(datetime(2026, 11, 11, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)

    for hour, ev in zip(today, events, strict=True):
        fields = parse_description(ev["description"])
        assert float(fields["price_kwh"]) == pytest.approx(hour["price"], abs=1e-4)
        assert float(fields["price_mwh"]) == pytest.approx(hour["price"] * 1000, abs=1e-2)
        assert fields["market"] == "DAM"
        assert fields["hours_in_day"] == "24"


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_ranks_are_consistent_within_day(hass, aioclient_mock):
    """Ранг 1 — найдешевша година, 24 — найдорожча, перцентиль по краях 0 і 100."""
    entry = await setup(hass, aioclient_mock)
    start, end = day_range(datetime(2026, 11, 11, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)

    rows = [parse_description(e["description"]) for e in events]
    prices = [float(r["price_mwh"]) for r in rows]
    ranks = [int(r["rank"]) for r in rows]
    assert sorted(ranks) == list(range(1, 25))

    cheapest = rows[ranks.index(1)]
    dearest = rows[ranks.index(24)]
    assert float(cheapest["price_mwh"]) == pytest.approx(min(prices))
    assert float(dearest["price_mwh"]) == pytest.approx(max(prices))
    assert cheapest["percentile"] == "0"
    assert dearest["percentile"] == "100"


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_summary_is_human_readable(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    start, end = day_range(datetime(2026, 11, 11, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)

    ev = events[0]
    fields = parse_description(ev["description"])
    assert ev["summary"] == f"{float(fields['price_kwh']):.2f} грн/кВт·год · ранг {fields['rank']}/24"


@pytest.mark.freeze_time(datetime(2026, 10, 25, 12, 0, tzinfo=KYIV))
async def test_fall_back_day_has_25_events(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, "10.2026")
    start, end = day_range(datetime(2026, 10, 25, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)
    assert len(events) == 25
    assert parse_description(events[0]["description"])["hours_in_day"] == "25"


@pytest.mark.freeze_time(datetime(2026, 3, 29, 12, 0, tzinfo=KYIV))
async def test_spring_forward_day_has_23_events(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock, "03.2026")
    start, end = day_range(datetime(2026, 3, 29, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, end)
    assert len(events) == 23
    assert parse_description(events[0]["description"])["hours_in_day"] == "23"
    # 03:00 за київським часом цієї доби не існує — вигаданих годин немає
    starts = {datetime.fromisoformat(e["start"]).strftime("%H:%M") for e in events}
    assert "03:00" not in starts


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_current_event_is_current_hour(hass, aioclient_mock):
    await hass.config.async_set_time_zone("Europe/Kyiv")
    entry = await setup(hass, aioclient_mock)
    state = hass.states.get(calendar_id(hass, entry))
    assert state.state == "on"
    # атрибут start_time — локальний час HA у форматі "YYYY-MM-DD HH:MM:SS"
    assert state.attributes["start_time"] == "2026-11-11 15:00:00"
    assert state.attributes["end_time"] == "2026-11-11 16:00:00"

    start = datetime(2026, 11, 11, 15, 30, tzinfo=KYIV)
    events = await get_events(hass, calendar_id(hass, entry), start, start + timedelta(minutes=1))
    assert len(events) == 1
    assert datetime.fromisoformat(events[0]["start"]) == datetime(
        2026, 11, 11, 15, 0, tzinfo=KYIV
    )


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_calendar_rank_matches_sensor(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    sensor_rank = hass.states.get(sensor_id(hass, entry, "current_hour_rank")).state

    start = datetime(2026, 11, 11, 15, 30, tzinfo=KYIV)
    events = await get_events(hass, calendar_id(hass, entry), start, start + timedelta(minutes=1))
    assert parse_description(events[0]["description"])["rank"] == sensor_rank


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_day_without_data_returns_no_events(hass, aioclient_mock):
    entry = await setup(hass, aioclient_mock)
    start, end = day_range(datetime(2026, 11, 20, tzinfo=KYIV))
    assert await get_events(hass, calendar_id(hass, entry), start, end) == []


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_range_outside_cache_returns_no_events(hass, aioclient_mock):
    """Хвилина в добі, якої немає в кеші (давно минулій), — подій немає."""
    entry = await setup(hass, aioclient_mock)
    moment = datetime(2026, 11, 1, 10, 0, tzinfo=KYIV)
    assert await get_events(hass, calendar_id(hass, entry), moment, moment + timedelta(minutes=1)) == []


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_range_across_two_days(hass, aioclient_mock):
    """Проміжок сьогодні+завтра віддає години обох діб, uid унікальні."""
    entry = await setup(hass, aioclient_mock)
    start, _ = day_range(datetime(2026, 11, 11, tzinfo=KYIV))
    events = await get_events(hass, calendar_id(hass, entry), start, start + timedelta(days=2))
    assert len(events) == 48
    starts = [datetime.fromisoformat(e["start"]) for e in events]
    assert starts == sorted(starts)
    assert len(set(starts)) == 48


@pytest.mark.freeze_time(datetime(2026, 11, 11, 15, 30, tzinfo=KYIV))
async def test_uid_is_stable_and_unique(hass, aioclient_mock):
    """uid = entry_id + ISO-початок години (сервіс get_events його не віддає)."""
    entry = await setup(hass, aioclient_mock)
    data = entry.runtime_data.data
    day = datetime(2026, 11, 11, tzinfo=KYIV).date()

    events = day_events(data, day, "DAM", entry.entry_id)
    again = day_events(data, day, "DAM", entry.entry_id)
    assert [e.uid for e in events] == [e.uid for e in again]
    assert len({e.uid for e in events}) == len(events) == 24
    assert events[0].uid == f"{entry.entry_id}_{events[0].start.isoformat()}"
