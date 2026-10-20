"""Инженерная проверка 20.10.2026: неожиданные числа не ломают расчёт, срок КП не начинается в прошлом."""
from datetime import date, datetime

import pytest

from app.domain.desired_date import proposal_range
from app.domain.enums import CountMode, Stage
from app.domain.models import StageNorm
from app.domain.quantity_breaks import MAX_BREAK_QUANTITY, parse_quantities
from app.domain.work_calendar import MACHINE_CALENDAR


@pytest.mark.parametrize("hours", [float("nan"), float("inf")])
def test_stage_hours_must_be_a_number(hours):
    # «nan»/«inf» из формы иначе сохраняются в маршрут, и расчёт срока по календарю зацикливается
    with pytest.raises(ValueError):
        StageNorm(Stage.MACHINING, hours, CountMode.PER_PIECE)


@pytest.mark.parametrize("hours", [float("nan"), float("inf")])
def test_calendar_rejects_endless_work(hours):
    with pytest.raises(ValueError):
        MACHINE_CALENDAR.add_hours(datetime(2026, 10, 20, 9), hours)


def test_quantity_breaks_have_upper_limit():
    assert parse_quantities(f"1, {MAX_BREAK_QUANTITY}") == [1, MAX_BREAK_QUANTITY]
    with pytest.raises(ValueError):
        parse_quantities(f"1, {MAX_BREAK_QUANTITY + 1}")


def test_proposal_range_never_starts_before_start_date():
    # срок «с 19.10 по 21.10» при расчёте 20.10: нижняя граница не раньше даты начала
    r = proposal_range(date(2026, 10, 20), datetime(2026, 10, 20, 12))
    assert r.earliest == date(2026, 10, 20) and r.latest == date(2026, 10, 21)


def test_proposal_range_never_starts_before_today():
    r = proposal_range(date(2026, 10, 12), datetime(2026, 10, 21, 12), not_before=date(2026, 10, 20))
    assert r.earliest == date(2026, 10, 20) and r.finish == date(2026, 10, 21)
