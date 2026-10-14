"""Рабочий календарь: N часов в смену, 5 дней в неделю, праздники не учитываются.

Станок: 6 ч на наладку и обработку; резка, слесарные этапы, контроль: 8 ч (ФТ-16).
Смена начинается в DAY_START_HOUR; рабочее окно дня — [начало, начало + N ч).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAY_START_HOUR = 8
WORKDAYS_PER_WEEK = 5
_EPS = 1e-9


@dataclass(frozen=True)
class WorkCalendar:
    hours_per_day: float
    day_start_hour: int = DAY_START_HOUR
    workdays: int = WORKDAYS_PER_WEEK

    def __post_init__(self):
        if not 0 < self.hours_per_day <= 24 - self.day_start_hour:
            raise ValueError("hours_per_day вне допустимого диапазона")
        if not 1 <= self.workdays <= 7:
            raise ValueError("workdays должен быть от 1 до 7")

    @property
    def hours_per_week(self) -> float:
        return self.hours_per_day * self.workdays

    def is_workday(self, day: date) -> bool:
        return day.weekday() < self.workdays

    def day_start(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, self.day_start_hour)

    def day_end(self, day: date) -> datetime:
        return self.day_start(day) + timedelta(hours=self.hours_per_day)

    def _next_workday(self, day: date) -> date:
        day += timedelta(days=1)
        while not self.is_workday(day):
            day += timedelta(days=1)
        return day

    def normalize(self, moment: datetime) -> datetime:
        """Ближайший рабочий момент не раньше заданного."""
        day = moment.date()
        if self.is_workday(day):
            if moment < self.day_start(day):
                return self.day_start(day)
            if moment < self.day_end(day):
                return moment
        return self.day_start(self._next_workday(day))

    def add_hours(self, start: datetime, hours: float) -> datetime:
        """Момент окончания работы длительностью hours рабочих часов."""
        if hours < 0:
            raise ValueError("hours не может быть отрицательным")
        current = self.normalize(start)
        remaining = float(hours)
        while True:
            available = (self.day_end(current.date()) - current).total_seconds() / 3600
            if remaining <= available + _EPS:
                return current + timedelta(hours=remaining)
            remaining -= available
            current = self.day_start(self._next_workday(current.date()))

    def split_by_day(self, start: datetime, end: datetime) -> dict[date, float]:
        """Рабочие часы интервала [start, end) по календарным дням."""
        result: dict[date, float] = {}
        if end <= start:
            return result
        day = start.date()
        while day <= end.date():
            if self.is_workday(day):
                lo = max(start, self.day_start(day))
                hi = min(end, self.day_end(day))
                if hi > lo:
                    result[day] = (hi - lo).total_seconds() / 3600
            day += timedelta(days=1)
        return result

    def hours_between(self, start: datetime, end: datetime) -> float:
        """Рабочие часы между моментами; ожидание и нерабочее время не входят (ФТ-21)."""
        return sum(self.split_by_day(start, end).values())


MACHINE_CALENDAR = WorkCalendar(hours_per_day=6)
BENCH_CALENDAR = WorkCalendar(hours_per_day=8)
