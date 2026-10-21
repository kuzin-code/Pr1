"""План не в прошлом: неначатая работа — не раньше «сейчас»; смена начинается в 09:00."""
from datetime import date, datetime

from app.domain.scheduling import PlanInput, build_plan, work_start
from app.domain.work_calendar import BENCH_CALENDAR, DAY_START_HOUR, MACHINE_CALENDAR

MON = date(2026, 10, 12)


def item(pid, started=False, **kw):
    return PlanInput(position_id=pid, order_id=1, order_start=MON, machine_code="VF2", cutting_hours=1.0,
                     machine_hours=3.0, bench_hours=2.0, inspection_hours=0.5, started=started, **kw)


def test_shift_starts_at_nine():
    assert DAY_START_HOUR == 9
    assert MACHINE_CALENDAR.day_start(MON) == datetime(2026, 10, 12, 9)
    assert MACHINE_CALENDAR.day_end(MON) == datetime(2026, 10, 12, 15)
    assert BENCH_CALENDAR.day_end(MON) == datetime(2026, 10, 12, 17)
    assert MACHINE_CALENDAR.normalize(datetime(2026, 10, 12, 8, 30)) == datetime(2026, 10, 12, 9)


def test_work_start_is_not_in_the_past():
    assert work_start(MON, None) == datetime(2026, 10, 12, 9)
    assert work_start(MON, datetime(2026, 10, 1, 12)) == datetime(2026, 10, 12, 9)  # заказ ещё не начат
    assert work_start(MON, datetime(2026, 10, 14, 11, 30)) == datetime(2026, 10, 14, 11, 30)
    assert work_start(MON, datetime(2026, 10, 14, 18)) == datetime(2026, 10, 15, 9)  # после смены
    assert work_start(MON, datetime(2026, 10, 17, 10)) == datetime(2026, 10, 19, 9)  # суббота


def test_unstarted_work_planned_from_now():
    now = datetime(2026, 10, 14, 11, 30)
    plan = build_plan([item(1)], {}, now=now)
    p = plan.positions[1]
    assert p.cutting_end == datetime(2026, 10, 14, 12, 30)
    assert p.machine_start == datetime(2026, 10, 14, 12, 30) and p.machine_start >= now
    assert all(s.start >= now for s in plan.slots)


def test_started_work_is_not_moved():
    now = datetime(2026, 10, 14, 11, 30)
    old = build_plan([item(1, started=True)], {}).positions[1]
    new = build_plan([item(1, started=True)], {}, now=now).positions[1]
    assert new == old and new.machine_start == datetime(2026, 10, 12, 10)


def test_queue_after_started_work_and_without_now_as_before():
    now = datetime(2026, 10, 12, 9, 30)
    plan = build_plan([item(1, started=True, approved_seq=1), item(2)], {}, now=now)
    first, second = plan.positions[1], plan.positions[2]
    assert second.machine_start == first.machine_end  # очередь станка сохраняется
    assert build_plan([item(2)], {}).positions[2].machine_start == datetime(2026, 10, 12, 10)
