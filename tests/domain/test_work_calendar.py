"""Рабочий календарь 5/2: станок 6 ч, слесарные этапы 8 ч (ФТ-16, ФТ-21)."""
from datetime import date, datetime

import pytest

from app.domain.work_calendar import MACHINE_CALENDAR, BENCH_CALENDAR, WorkCalendar

MON = datetime(2026, 10, 12, 8, 0)  # понедельник


def test_add_hours_within_one_shift():
    assert MACHINE_CALENDAR.add_hours(MON, 6) == datetime(2026, 10, 12, 14, 0)


def test_add_hours_rolls_to_next_day():
    assert MACHINE_CALENDAR.add_hours(MON, 7) == datetime(2026, 10, 13, 9, 0)


def test_add_hours_skips_weekend():
    friday_noon = datetime(2026, 10, 16, 12, 0)
    assert MACHINE_CALENDAR.add_hours(friday_noon, 6) == datetime(2026, 10, 19, 12, 0)


def test_start_on_weekend_moves_to_monday():
    saturday = datetime(2026, 10, 17, 10, 0)
    assert MACHINE_CALENDAR.add_hours(saturday, 1) == datetime(2026, 10, 19, 9, 0)


def test_start_after_shift_moves_to_next_morning():
    evening = datetime(2026, 10, 12, 20, 0)
    assert MACHINE_CALENDAR.add_hours(evening, 2) == datetime(2026, 10, 13, 10, 0)


def test_zero_hours_returns_normalized_start():
    assert MACHINE_CALENDAR.add_hours(datetime(2026, 10, 17, 10, 0), 0) == datetime(2026, 10, 19, 8, 0)


def test_bench_calendar_has_8_hour_day():
    assert BENCH_CALENDAR.add_hours(MON, 8) == datetime(2026, 10, 12, 16, 0)
    assert BENCH_CALENDAR.add_hours(MON, 12) == datetime(2026, 10, 13, 12, 0)


def test_hours_between_over_weekend():
    start = datetime(2026, 10, 16, 10, 0)  # пятница
    end = datetime(2026, 10, 19, 10, 0)  # понедельник
    assert MACHINE_CALENDAR.hours_between(start, end) == pytest.approx(6)


def test_hours_between_over_night():
    start = datetime(2026, 10, 12, 13, 0)
    end = datetime(2026, 10, 13, 9, 0)
    assert MACHINE_CALENDAR.hours_between(start, end) == pytest.approx(2)


def test_hours_between_reversed_is_zero():
    assert MACHINE_CALENDAR.hours_between(MON, datetime(2026, 10, 11)) == 0


def test_hours_between_is_inverse_of_add_hours():
    for hours in (0.5, 3, 6, 13.25, 40):
        end = BENCH_CALENDAR.add_hours(MON, hours)
        assert BENCH_CALENDAR.hours_between(MON, end) == pytest.approx(hours)


def test_day_start_from_date():
    assert MACHINE_CALENDAR.day_start(date(2026, 10, 12)) == MON


def test_hours_per_day_split():
    start = datetime(2026, 10, 12, 12, 0)
    end = datetime(2026, 10, 13, 10, 0)
    assert MACHINE_CALENDAR.split_by_day(start, end) == {
        date(2026, 10, 12): pytest.approx(2),
        date(2026, 10, 13): pytest.approx(2),
    }


def test_invalid_calendar():
    with pytest.raises(ValueError):
        WorkCalendar(hours_per_day=0)
