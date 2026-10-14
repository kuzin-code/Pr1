"""Длительность групп этапов по факту в рабочих часах (ФТ-21)."""
from datetime import datetime

from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR


def machine_group_duration(order_start: datetime, prev_machine_end: datetime | None,
                           marked_end: datetime) -> float:
    """От более позднего из (начало заказа, окончание предыдущей работы на станке)."""
    possible_start = max(order_start, prev_machine_end) if prev_machine_end else order_start
    return MACHINE_CALENDAR.hours_between(possible_start, marked_end)


def bench_group_duration(machine_end: datetime, marked_end: datetime) -> float:
    """От окончания станочной группы той же партии."""
    return BENCH_CALENDAR.hours_between(machine_end, marked_end)
