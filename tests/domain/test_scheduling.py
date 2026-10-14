"""ФТ-15 (очередь), ФТ-16 (срок), ФТ-17 (загрузка), ФТ-21 (длительность факта), ФТ-22, ФТ-23."""
from datetime import date, datetime

import pytest

from app.domain.enums import FactKind
from app.domain.facts import bench_group_duration, machine_group_duration
from app.domain.load import daily_load, weekly_load
from app.domain.notifications import due_events
from app.domain.scheduling import PlanInput, build_plan, order_deadline

MON = date(2026, 10, 12)


def item(pid, seq=None, machine="VF2", cut=2, mach=6, bench=8, insp=1, start=MON, **facts):
    return PlanInput(position_id=pid, order_id=1, order_start=start, machine_code=machine,
                     cutting_hours=cut, machine_hours=mach, bench_hours=bench, inspection_hours=insp,
                     approved_seq=seq, **facts)


def test_single_position_sequential_stages():
    plan = build_plan([item(1, seq=1)], {})
    p = plan.positions[1]
    assert p.cutting_end == datetime(2026, 10, 12, 10, 0)  # резка 2 ч по 8-часовому дню
    assert p.machine_start == datetime(2026, 10, 12, 10, 0)
    assert p.machine_end == datetime(2026, 10, 13, 10, 0)  # 4 ч в пн (до 14:00) + 2 ч во вт
    assert p.bench_end == datetime(2026, 10, 14, 10, 0)
    assert p.finish == datetime(2026, 10, 14, 11, 0)
    assert not p.preliminary


def test_second_position_on_same_machine_waits():
    plan = build_plan([item(2, seq=2), item(1, seq=1)], {})
    first, second = plan.positions[1], plan.positions[2]
    assert second.machine_start == first.machine_end
    assert second.machine_possible_start == first.machine_end


def test_busy_until_shifts_start():
    busy = {"VF2": datetime(2026, 10, 14, 12, 0)}
    p = build_plan([item(1, seq=1)], busy).positions[1]
    assert p.machine_start == datetime(2026, 10, 14, 12, 0)
    assert p.machine_possible_start == datetime(2026, 10, 14, 12, 0)


def test_unapproved_positions_preliminary_and_after_approved():
    plan = build_plan([item(1), item(2, seq=1)], {})
    assert plan.positions[1].preliminary
    assert plan.positions[1].machine_start == plan.positions[2].machine_end


def test_other_machine_independent():
    plan = build_plan([item(1, seq=1), item(2, seq=2, machine="SMM")], {})
    assert plan.positions[2].machine_start == plan.positions[1].machine_start


def test_order_deadline_is_max_of_positions():
    plan = build_plan([item(1, seq=1), item(2, seq=2)], {})
    assert order_deadline(plan, [1, 2]) == plan.positions[2].finish


def test_fact_later_than_plan_shifts_position_and_next():
    base = build_plan([item(1, seq=1), item(2, seq=2)], {})
    late = datetime(2026, 10, 15, 9, 0)
    shifted = build_plan([item(1, seq=1, fact_machine_end=late), item(2, seq=2)], {})
    assert shifted.positions[1].machine_end == late
    assert shifted.positions[1].finish > base.positions[1].finish
    assert shifted.positions[2].machine_start == late
    assert shifted.positions[2].finish > base.positions[2].finish


def test_position_without_machine_not_planned():
    plan = build_plan([item(1, seq=1, machine=None)], {})
    assert 1 not in plan.positions


def test_daily_and_weekly_load():
    plan = build_plan([item(1, seq=1, mach=10)], {})
    daily = daily_load(plan.slots)["VF2"]
    assert daily[date(2026, 10, 12)].hours == pytest.approx(4)
    assert daily[date(2026, 10, 12)].percent == pytest.approx(4 / 6 * 100)
    assert daily[date(2026, 10, 13)].hours == pytest.approx(6)
    weekly = weekly_load(plan.slots)["VF2"]
    assert weekly[(2026, 42)].hours == pytest.approx(10)
    assert weekly[(2026, 42)].percent == pytest.approx(10 / 30 * 100)


def test_machine_duration_over_weekend_and_night():
    order_start = datetime(2026, 10, 16, 8, 0)  # пятница
    prev_end = datetime(2026, 10, 16, 12, 0)
    marked = datetime(2026, 10, 19, 10, 0)  # понедельник
    # пт 12:00–14:00 = 2 ч, пн 08:00–10:00 = 2 ч
    assert machine_group_duration(order_start, prev_end, marked) == pytest.approx(4)
    assert machine_group_duration(order_start, None, marked) == pytest.approx(8)


def test_bench_duration_from_machine_end():
    assert bench_group_duration(datetime(2026, 10, 12, 15, 0), datetime(2026, 10, 13, 10, 0)) == pytest.approx(3)


def test_due_notifications_for_master():
    plan = build_plan([item(1, seq=1), item(2)], {})
    events = due_events(plan, now=datetime(2026, 10, 13, 12, 0))
    assert [(e.position_id, e.kind) for e in events] == [(1, FactKind.MACHINE)]
    later = due_events(plan, now=datetime(2026, 10, 20))
    assert {e.kind for e in later if e.position_id == 1} == set(FactKind)
    assert all(e.position_id == 1 for e in later)  # предварительные позиции не уведомляют
