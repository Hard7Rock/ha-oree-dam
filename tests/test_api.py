"""Тести розбору oree на справжніх відповідях сервера (tests/fixtures)."""

from datetime import date, datetime, timezone
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "oree_dam"))
import api  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def raw(month: str) -> bytes:
    return (FIX / f"dv_{month}.json").read_bytes()


def test_ordinary_month_has_24h_days():
    days = api.parse_response(raw("09.2026"))
    assert len(days) == 30
    assert all(len(s) == 24 for s in days.values())


def test_spring_forward_day_has_23_hours():
    days = api.parse_response(raw("03.2026"))
    d = days[date(2026, 3, 29)]
    assert len(d) == 23
    # 00:00 за Києвом (UTC+2) = 22:00 UTC попереднього дня
    assert d[0].start == datetime(2026, 3, 28, 22, tzinfo=timezone.utc)
    # перша година після переходу: 03:00 за Києвом вже UTC+3 → 00:00 UTC
    starts_local = [s.start.astimezone(api.KYIV).hour for s in d]
    assert 3 not in starts_local  # година 03:00 у цю добу не існує
    assert starts_local[:4] == [0, 1, 2, 4]


def test_fall_back_day_has_25_hours():
    days = api.parse_response(raw("10.2026"))
    d = days[date(2026, 10, 25)]
    assert len(d) == 25
    local_hours = [s.start.astimezone(api.KYIV).hour for s in d]
    assert local_hours.count(3) == 2  # 03:00 буває двічі
    # сусідні дні того ж місяця — по 24 години, попри 25 колонок
    assert len(days[date(2026, 10, 24)]) == 24
    assert len(days[date(2026, 10, 26)]) == 24


def test_slots_are_contiguous_utc():
    for m in ("03.2026", "09.2026", "10.2026", "11.2026"):
        for slots in api.parse_response(raw(m)).values():
            for a, b in zip(slots, slots[1:]):
                assert b.start == a.end


def test_known_values_and_units():
    days = api.parse_response(raw("11.2026"))
    first = days[date(2026, 11, 1)][0]
    assert first.price_mwh == 7000.00
    assert first.price_kwh == pytest.approx(7.0)
    assert first.as_dict()["start"].startswith("2026-11-01T00:00:00+02:00")


def test_mismatched_hours_rejected_not_guessed():
    vals = [1000.0] * 22 + [None, None]  # 22 ціни на 24-годинну добу
    with pytest.raises(api.OreeFormatError):
        api.build_slots(date(2026, 9, 10), vals)


def test_out_of_range_price_rejected():
    vals = [1000.0] * 23 + [10_000_000.0]
    with pytest.raises(api.OreeFormatError):
        api.build_slots(date(2026, 9, 10), vals)


def test_unpublished_day_is_skipped():
    assert api.build_slots(date(2026, 9, 10), [None] * 24) == []


def test_garbage_response():
    with pytest.raises(api.OreeFormatError):
        api.decode_response(b"<html>maintenance</html>")
    with pytest.raises(api.OreeFormatError):
        api.decode_response(json.dumps({"x": 1}).encode())


def test_months_needed_month_boundary():
    end = datetime(2026, 11, 30, 15, tzinfo=api.KYIV)
    assert api.months_needed(end) == ["11.2026", "12.2026"]
    mid = datetime(2026, 11, 15, 15, tzinfo=api.KYIV)
    assert api.months_needed(mid) == ["11.2026"]


def test_market_validation():
    assert api.form_data("11.2026") == {"date": "11.2026", "market": "DAM", "zone": "IPS"}
    with pytest.raises(ValueError):
        api.form_data("11.2026", "XXX")
