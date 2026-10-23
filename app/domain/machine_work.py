"""Объём работы на станке для загрузки (ФТ-17): плановые часы и фактическая работа по отметкам «Начал»/«Закончил».

Правило:
- позиция, по которой на станке ничего не отмечено, — плановые часы наладки и обработки, распределённые
  по рабочим часам её полосы плана;
- отмеченная операция — фактическая длительность от «Начал» до «Закончил» (у идущей — до «сейчас») в рабочих
  часах станка; работа вне смены (рабочих часов в интервале нет) — по часам на часах, в тот день, когда делалась;
- операция, «висевшая» начатой (простой), засчитывается не больше чем план × IDLE_CAP_FACTOR: превышение
  плана до этого предела — реальная работа, сверх него — простой, а не загрузка станка;
- неотмеченный остаток плана начатой позиции (идущая операция сверх сделанного и ещё не начатые станочные
  операции) — плановыми часами от «сейчас» (или от последней отметки) по календарю станка.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.scheduling import MachineSlot
from app.domain.work_calendar import MACHINE_CALENDAR, WorkCalendar

IDLE_CAP_FACTOR = 1.5


@dataclass(frozen=True)
class Work:
    """Объём работы hours на станке в интервале [start, end]."""
    machine_code: str
    position_id: int
    start: datetime
    end: datetime
    hours: float


@dataclass(frozen=True)
class OpMark:
    """Станочная операция позиции на станке с плановыми часами и отметками (без отметок — ещё не начата)."""
    machine_code: str
    position_id: int
    planned_hours: float
    started_at: datetime | None = None
    finished_at: datetime | None = None


def _clock_by_day(start: datetime, end: datetime) -> dict[date, float]:
    result: dict[date, float] = {}
    day = start.date()
    while day <= end.date():
        lo = max(start, datetime.combine(day, datetime.min.time()))
        hi = min(end, datetime.combine(day + timedelta(days=1), datetime.min.time()))
        if hi > lo:
            result[day] = (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return result


def spread(work: Work, calendar: WorkCalendar = MACHINE_CALENDAR) -> dict[date, float]:
    """Часы работы по дням: пропорционально рабочим часам интервала, вне смены — часам на часах."""
    if work.hours <= 0:
        return {}
    weights = calendar.split_by_day(work.start, work.end) or _clock_by_day(work.start, work.end)
    if not weights:
        return {work.start.date(): work.hours}
    total = sum(weights.values())
    return {day: work.hours * w / total for day, w in weights.items()}


def measured_hours(start: datetime, end: datetime, calendar: WorkCalendar = MACHINE_CALENDAR) -> float:
    """Рабочие часы интервала; если интервал целиком вне смены — часы на часах."""
    if end <= start:
        return 0.0
    hours = calendar.hours_between(start, end)
    return hours if hours > 0 else (end - start).total_seconds() / 3600


def counted_hours(mark: OpMark, end: datetime, calendar: WorkCalendar = MACHINE_CALENDAR) -> float:
    measured = measured_hours(mark.started_at, end, calendar)
    if mark.planned_hours > 0:
        return min(measured, mark.planned_hours * IDLE_CAP_FACTOR)
    return measured


def _marked_work(slot: MachineSlot, marks: list[OpMark], now: datetime | None,
                 calendar: WorkCalendar) -> list[Work]:
    works, remaining, last = [], 0.0, None
    for mark in marks:
        if mark.started_at is None:
            remaining += mark.planned_hours
            continue
        end = mark.finished_at or max(now or mark.started_at, mark.started_at)
        done = counted_hours(mark, end, calendar)
        works.append(Work(slot.machine_code, slot.position_id, mark.started_at, end, done))
        if mark.finished_at is None:
            remaining += max(mark.planned_hours - done, 0.0)
        last = max(last, end) if last else end
    if remaining > 0:
        begin = calendar.normalize(max(now, last) if now else last)
        works.append(Work(slot.machine_code, slot.position_id, begin, calendar.add_hours(begin, remaining),
                          remaining))
    return works


def machine_work(slots: list[MachineSlot], marks: list[OpMark], now: datetime | None = None,
                 calendar: WorkCalendar = MACHINE_CALENDAR) -> list[Work]:
    """Объём работы по полосам плана с учётом отметок операций на том же станке."""
    by_slot: dict[tuple[int, str], list[OpMark]] = defaultdict(list)
    for mark in marks:
        by_slot[(mark.position_id, mark.machine_code)].append(mark)
    works = []
    for slot in slots:
        own = by_slot.get((slot.position_id, slot.machine_code), [])
        if any(m.started_at for m in own):
            works.extend(_marked_work(slot, own, now, calendar))
        else:
            hours = slot.hours if slot.hours is not None else calendar.hours_between(slot.start, slot.end)
            works.append(Work(slot.machine_code, slot.position_id, slot.start, slot.end, hours))
    return works
