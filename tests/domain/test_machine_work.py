"""Загрузка станка = объём работы: план — плановыми часами, факт — по отметкам, простой не считается."""
from datetime import date, datetime

import pytest

from app.domain.load import daily_load, weekly_load
from app.domain.machine_work import IDLE_CAP_FACTOR, OpMark, Work, machine_work, spread
from app.domain.scheduling import MachineSlot

TUE, WED, THU, FRI = (date(2026, 10, d) for d in (20, 21, 22, 23))


def slot(start, end, hours, pid=1, code="VF2"):
    return MachineSlot(code, pid, start, end, False, hours)


def total(items, code="VF2"):
    return sum(c.hours for c in daily_load(items).get(code, {}).values())


def test_planned_slot_counts_planned_hours_not_duration():
    """Полоса плана с фактом окончания длиннее плана — в загрузку идут плановые часы."""
    s = slot(datetime(2026, 10, 20, 9), datetime(2026, 10, 23, 15), 1.1)
    assert total([s]) == pytest.approx(1.1)
    week = weekly_load([s])["VF2"][(2026, 43)]
    assert week.hours == pytest.approx(1.1)


def test_hanging_operation_counted_up_to_cap():
    """«Обработку» начали во вторник вечером, «Закончил» нажали в пятницу — это простой, а не 18 ч работы."""
    s = slot(datetime(2026, 10, 20, 19, 28), datetime(2026, 10, 23, 17, 57), 1.1, pid=8)
    marks = [OpMark("VF2", 8, 0.5, datetime(2026, 10, 20, 19, 28), datetime(2026, 10, 20, 19, 30)),
             OpMark("VF2", 8, 0.6, datetime(2026, 10, 20, 19, 56), datetime(2026, 10, 23, 17, 57))]
    works = machine_work([s], marks)
    hours = total(works)
    assert hours == pytest.approx(2 / 60 + 0.6 * IDLE_CAP_FACTOR)
    assert hours < 1.1 * IDLE_CAP_FACTOR + 0.1


def test_work_after_shift_counted_on_its_day():
    """Наладка 18:06–19:29 после смены станка — 1,38 ч в пятницу, а не 0."""
    s = slot(datetime(2026, 10, 23, 18, 6), datetime(2026, 10, 23, 21), 4.05, pid=18)
    marks = [OpMark("VF2", 18, 2.0, datetime(2026, 10, 23, 18, 6), datetime(2026, 10, 23, 19, 29)),
             OpMark("VF2", 18, 3.4, datetime(2026, 10, 23, 20, 59), datetime(2026, 10, 23, 21, 0))]
    daily = daily_load(machine_work([s], marks))["VF2"]
    assert set(daily) == {FRI}
    assert daily[FRI].hours == pytest.approx(83 / 60 + 1 / 60)


def test_in_progress_operation_counts_done_part_and_rest_of_plan():
    now = datetime(2026, 10, 21, 11)
    s = slot(datetime(2026, 10, 21, 9), datetime(2026, 10, 21, 13), 4.0)
    marks = [OpMark("VF2", 1, 1.0, datetime(2026, 10, 21, 9), datetime(2026, 10, 21, 10)),
             OpMark("VF2", 1, 3.0, datetime(2026, 10, 21, 10), None)]
    daily = daily_load(machine_work([s], marks, now))["VF2"]
    assert daily[WED].hours == pytest.approx(4.0)  # 1 ч наладка + 1 ч обработки + 2 ч остатка плана


def test_rest_of_plan_after_started_setup_goes_from_now():
    now = datetime(2026, 10, 21, 14)
    s = slot(datetime(2026, 10, 21, 9), datetime(2026, 10, 22, 10), 4.0)
    marks = [OpMark("VF2", 1, 1.0, datetime(2026, 10, 21, 9), datetime(2026, 10, 21, 10)),
             OpMark("VF2", 1, 3.0)]
    daily = daily_load(machine_work([s], marks, now))["VF2"]
    assert daily[WED].hours == pytest.approx(2.0)  # наладка 1 ч + остаток с 14:00 до 15:00
    assert daily[THU].hours == pytest.approx(2.0)


def test_marks_on_other_machine_do_not_replace_plan():
    s = slot(datetime(2026, 10, 21, 9), datetime(2026, 10, 21, 11), 2.0)
    marks = [OpMark("SMM", 1, 1.0, datetime(2026, 10, 21, 9), datetime(2026, 10, 21, 10))]
    assert total(machine_work([s], marks)) == pytest.approx(2.0)


def test_spread_falls_back_to_clock_hours_off_shift():
    w = Work("VF2", 1, datetime(2026, 10, 24, 10), datetime(2026, 10, 24, 12), 2.0)  # суббота
    assert spread(w) == {date(2026, 10, 24): pytest.approx(2.0)}
