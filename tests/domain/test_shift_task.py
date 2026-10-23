"""Сменное задание: операции станка на смену, обрезанные по границам смены."""
from datetime import date, datetime

import pytest

from app.domain.scheduling import MachineSlot
from app.domain.shift_task import SlotOp, shift_task, split_slot

MON, TUE, SAT = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 17)


def slot(pid, start, end, hours, machine="VF2", preliminary=False):
    return MachineSlot(machine, pid, start, end, preliminary, hours)


def test_split_slot_sequential_by_calendar():
    s = slot(1, datetime(2026, 10, 12, 9), datetime(2026, 10, 13, 12), 9)
    parts = split_slot(s, [SlotOp(10, "setup", 1), SlotOp(11, "milling", 8)])
    assert [(p[1], p[2]) for p in parts] == [
        (datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 10)),
        (datetime(2026, 10, 12, 10), datetime(2026, 10, 13, 12))]


def test_shift_clipped_to_boundaries():
    slots = [slot(1, datetime(2026, 10, 12, 9), datetime(2026, 10, 13, 12), 9),
             slot(2, datetime(2026, 10, 13, 12), datetime(2026, 10, 13, 14), 2),
             slot(3, datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 15), 6, machine="ST20Y")]
    ops = {1: [SlotOp(10, "setup", 1), SlotOp(11, "milling", 8)]}
    mon = shift_task(slots, ops, "VF2", MON)
    assert (mon.start, mon.end) == (datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 15))
    assert [(l.op.op_id, l.hours, l.from_previous, l.continues) for l in mon.lines] == [
        (10, 1, False, False), (11, 5, False, True)]
    tue = shift_task(slots, ops, "VF2", TUE)
    assert [(l.position_id, l.op.op_id, l.hours, l.from_previous) for l in tue.lines] == [
        (1, 11, 3, True), (2, None, 2, False)]
    assert tue.lines[1].op.operation == "machine"  # позиция без операций — одной строкой
    assert tue.hours == pytest.approx(5)


def test_weekend_and_other_day_empty():
    slots = [slot(1, datetime(2026, 10, 12, 9), datetime(2026, 10, 12, 11), 2)]
    sat = shift_task(slots, {}, "VF2", SAT)
    assert sat.start is None and sat.lines == []
    assert shift_task(slots, {}, "VF2", TUE).lines == []
