"""Сменное задание на станок: операции станка на смену из текущего плана, обрезанные по границам смены.

Правило:
- полоса плана позиции на станке (наладка + обработка) раскладывается на операции станка подряд,
  по их плановым часам и календарю станка;
- в задание попадает часть операции, приходящаяся на смену [начало, конец) выбранного дня (work_calendar);
- у операции, начатой раньше смены или кончающейся позже, это видно по признакам «с прошлой смены» /
  «продолжится в следующей»;
- в выходной смены нет — задание пустое.
"""
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.scheduling import MachineSlot
from app.domain.work_calendar import MACHINE_CALENDAR, WorkCalendar


@dataclass(frozen=True)
class SlotOp:
    """Операция позиции на станке с плановыми часами (op_id None — операций ещё нет, позиция не утверждена)."""
    op_id: int | None
    operation: str
    planned_hours: float
    status: str = ""


@dataclass(frozen=True)
class ShiftLine:
    position_id: int
    op: SlotOp
    start: datetime  # в пределах смены
    end: datetime
    hours: float  # рабочие часы операции в эту смену
    from_previous: bool  # начата до смены
    continues: bool  # продолжится после смены
    preliminary: bool


@dataclass(frozen=True)
class Shift:
    day: date
    start: datetime | None  # None — выходной
    end: datetime | None
    lines: list[ShiftLine]

    @property
    def hours(self) -> float:
        return sum(line.hours for line in self.lines)


def split_slot(slot: MachineSlot, ops: list[SlotOp],
               calendar: WorkCalendar = MACHINE_CALENDAR) -> list[tuple[SlotOp, datetime, datetime]]:
    """Операции подряд от начала полосы; последняя кончается не позже конца полосы."""
    result, moment = [], slot.start
    for i, op in enumerate(ops):
        if i == len(ops) - 1:
            end = max(slot.end, moment)
        else:
            end = max(min(calendar.add_hours(moment, op.planned_hours), slot.end), moment)
        result.append((op, moment, end))
        moment = end
    return result


def shift_task(slots: list[MachineSlot], ops_by_position: dict[int, list[SlotOp]], machine: str, day: date,
               calendar: WorkCalendar = MACHINE_CALENDAR) -> Shift:
    if not calendar.is_workday(day):
        return Shift(day, None, None, [])
    lo, hi = calendar.day_start(day), calendar.day_end(day)
    lines = []
    for slot in sorted((s for s in slots if s.machine_code == machine), key=lambda s: s.start):
        planned = slot.hours if slot.hours is not None else calendar.hours_between(slot.start, slot.end)
        ops = ops_by_position.get(slot.position_id) or [SlotOp(None, "machine", planned)]
        for op, start, end in split_slot(slot, ops, calendar):
            a, b = max(start, lo), min(end, hi)
            hours = calendar.hours_between(a, b) if b > a else 0.0
            if hours <= 0:
                continue
            lines.append(ShiftLine(slot.position_id, op, a, b, round(hours, 3), start < lo, end > hi,
                                   slot.preliminary))
    return Shift(day, lo, hi, lines)
