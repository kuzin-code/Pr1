"""Загрузка станков по дням и неделям в часах и % от рабочего графика (ФТ-17)."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.domain.scheduling import MachineSlot
from app.domain.work_calendar import MACHINE_CALENDAR, WorkCalendar


@dataclass(frozen=True)
class LoadCell:
    hours: float
    capacity: float

    @property
    def percent(self) -> float:
        return self.hours / self.capacity * 100 if self.capacity else 0.0


def daily_load(slots: list[MachineSlot], calendar: WorkCalendar = MACHINE_CALENDAR
               ) -> dict[str, dict[date, LoadCell]]:
    hours: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for slot in slots:
        for day, h in calendar.split_by_day(slot.start, slot.end).items():
            hours[slot.machine_code][day] += h
    return {
        m: {d: LoadCell(h, calendar.hours_per_day) for d, h in sorted(days.items())}
        for m, days in hours.items()
    }


def weekly_load(slots: list[MachineSlot], calendar: WorkCalendar = MACHINE_CALENDAR
                ) -> dict[str, dict[tuple[int, int], LoadCell]]:
    result = {}
    for machine, days in daily_load(slots, calendar).items():
        weeks: dict[tuple[int, int], float] = defaultdict(float)
        for day, cell in days.items():
            iso = day.isocalendar()
            weeks[(iso.year, iso.week)] += cell.hours
        result[machine] = {w: LoadCell(h, calendar.hours_per_week) for w, h in sorted(weeks.items())}
    return result
