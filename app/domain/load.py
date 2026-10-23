"""Загрузка станков по дням и неделям в часах и % от рабочего графика (ФТ-17).

Загрузка — объём работы на станке (см. machine_work), а не календарная длительность полосы плана.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.domain.machine_work import Work, machine_work, spread
from app.domain.scheduling import MachineSlot
from app.domain.work_calendar import MACHINE_CALENDAR, WorkCalendar


@dataclass(frozen=True)
class LoadCell:
    hours: float
    capacity: float

    @property
    def percent(self) -> float:
        return self.hours / self.capacity * 100 if self.capacity else 0.0


def _works(items: list[MachineSlot | Work], calendar: WorkCalendar) -> list[Work]:
    slots = [i for i in items if isinstance(i, MachineSlot)]
    return [i for i in items if isinstance(i, Work)] + machine_work(slots, [], None, calendar)


def daily_load(items: list[MachineSlot | Work], calendar: WorkCalendar = MACHINE_CALENDAR
               ) -> dict[str, dict[date, LoadCell]]:
    """Полосы плана — плановыми часами; Work — готовым объёмом работы (факт и остаток плана)."""
    hours: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for work in _works(items, calendar):
        for day, h in spread(work, calendar).items():
            hours[work.machine_code][day] += h
    return {
        m: {d: LoadCell(h, calendar.hours_per_day) for d, h in sorted(days.items())}
        for m, days in hours.items()
    }


def weekly_load(items: list[MachineSlot | Work], calendar: WorkCalendar = MACHINE_CALENDAR
                ) -> dict[str, dict[tuple[int, int], LoadCell]]:
    result = {}
    for machine, days in daily_load(items, calendar).items():
        weeks: dict[tuple[int, int], float] = defaultdict(float)
        for day, cell in days.items():
            iso = day.isocalendar()
            weeks[(iso.year, iso.week)] += cell.hours
        result[machine] = {w: LoadCell(h, calendar.hours_per_week) for w, h in sorted(weeks.items())}
    return result
