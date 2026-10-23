"""Включённый PLAN_FROM_MARKS — станочная работа по факту: начало — отметка «Начал» первой станочной операции; полоса не бывает конец < начало."""
from datetime import date, datetime

from functools import partial

from app.domain.scheduling import PlanInput
from app.domain.scheduling import build_plan as _build_plan
from app.domain.work_calendar import MACHINE_CALENDAR

build_plan = partial(_build_plan, from_marks=True)  # логика «по факту» явно включена
MON = date(2026, 10, 12)
FRI_EVENING = datetime(2026, 10, 16, 18, 0)  # пятница после смены станка


def item(pid, seq=None, machine="VF2", mach=3.0, start=MON, **kw):
    return PlanInput(position_id=pid, order_id=1, order_start=start, machine_code=machine, cutting_hours=1.0,
                     machine_hours=mach, bench_hours=2.0, inspection_hours=0.5, approved_seq=seq, **kw)


def assert_slots_valid(plan):
    assert all(s.end >= s.start for s in plan.slots), [(s.position_id, s.start, s.end) for s in plan.slots]


def test_machine_start_is_actual_start_of_first_machine_operation():
    actual = datetime(2026, 10, 16, 15, 6)  # наладку начали после смены, план сдвинул бы на понедельник
    fact = datetime(2026, 10, 16, 21, 0)
    p = build_plan([item(1, seq=1, started=True, machine_started=(("VF2", actual),), fact_machine_end=fact)],
                   {"VF2": datetime(2026, 10, 16, 14, 57)}, now=FRI_EVENING)
    slot = p.slots[0]
    assert (slot.start, slot.end) == (actual, fact)
    assert p.positions[1].machine_start == actual and p.positions[1].machine_end == fact


def test_fact_end_before_planned_start_without_marks_keeps_bar_valid():
    """Факт введён без отметок «Начал», окончание раньше даты начала заказа — полоса заканчивается на факте."""
    fact = datetime(2026, 10, 7, 15, 54)
    plan = build_plan([item(1, seq=1, started=True, fact_machine_end=fact, mach=4.0)], {})
    assert_slots_valid(plan)
    slot = plan.slots[0]
    assert slot.end == fact
    assert MACHINE_CALENDAR.hours_between(slot.start, slot.end) == 4.0  # длительность — плановая
    assert plan.positions[1].machine_start == slot.start


def test_started_position_occupies_machine_before_unstarted_queue():
    """Неначатая позиция раньше по очереди не занимает станок поверх уже идущей работы."""
    actual = datetime(2026, 10, 14, 10, 0)
    now = datetime(2026, 10, 14, 11, 0)
    plan = build_plan([item(1, seq=1), item(2, seq=2, started=True, machine_started=(("VF2", actual),))],
                      {}, now=now)
    first, started = plan.positions[1], plan.positions[2]
    assert started.machine_start == actual
    assert started.machine_end == MACHINE_CALENDAR.add_hours(actual, 3.0)
    assert first.machine_start >= started.machine_end and first.machine_start >= now
    assert_slots_valid(plan)


def test_queue_after_finished_position_counts_from_fact_end():
    actual, fact = datetime(2026, 10, 13, 9, 30), datetime(2026, 10, 13, 10, 0)
    plan = build_plan([item(1, seq=1, started=True, machine_started=(("VF2", actual),), fact_machine_end=fact),
                       item(2, seq=2, started=True, machine_started=(("VF2", datetime(2026, 10, 13, 10, 5)),))],
                      {})
    assert plan.positions[1].machine_end == fact
    assert plan.positions[2].machine_start == datetime(2026, 10, 13, 10, 5)
    assert plan.positions[2].machine_possible_start <= plan.positions[2].machine_start


def test_second_machine_slot_with_fact_and_actual_starts():
    a1, a2, fact = datetime(2026, 10, 13, 9), datetime(2026, 10, 13, 13), datetime(2026, 10, 13, 14)
    plan = build_plan([item(1, seq=1, started=True, extra_machines=(("SMM", 2.0),),
                            machine_started=(("VF2", a1), ("SMM", a2)), fact_machine_end=fact)], {})
    assert_slots_valid(plan)
    vf2, smm = plan.slots
    assert vf2.start == a1 and smm.start == a2 and smm.end == fact
    assert vf2.end <= smm.start


def test_second_machine_fact_earlier_than_planned_chain():
    fact = datetime(2026, 10, 7, 12, 0)
    plan = build_plan([item(1, seq=1, started=True, extra_machines=(("SMM", 2.0),), fact_machine_end=fact)], {})
    assert_slots_valid(plan)
    vf2, smm = plan.slots
    assert smm.end == fact and vf2.end <= smm.start


def test_unstarted_still_not_in_past():
    now = datetime(2026, 10, 14, 11, 30)
    plan = build_plan([item(1, seq=1, started=True, fact_machine_end=datetime(2026, 10, 7, 12)), item(2, seq=2)],
                      {}, now=now)
    assert plan.positions[2].machine_start >= now
    assert_slots_valid(plan)
