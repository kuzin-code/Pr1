"""Желаемая дата заказчика против расчётной даты изготовления: успеваем ли и на сколько рабочих дней (5/2)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class DesiredCheck:
    status: str  # ok — успеваем; late — не успеваем; unknown — срок не рассчитан; none — желаемой даты нет
    workdays: int = 0

    @property
    def label(self) -> str:
        if self.status == "ok":
            return f"успеваем, запас {self.workdays} раб. дн." if self.workdays else "успеваем точно в срок"
        if self.status == "late":
            return f"не успеваем на {self.workdays} раб. дн."
        if self.status == "unknown":
            return "срок не рассчитан"
        return ""


def _workdays_between(first: date, last: date) -> int:
    """Рабочие дни после first до last включительно."""
    days, day = 0, first
    while day < last:
        day += timedelta(days=1)
        days += day.weekday() < 5
    return days


def check_desired(calculated: datetime | None, desired: date | None) -> DesiredCheck:
    if desired is None:
        return DesiredCheck("none")
    if calculated is None:
        return DesiredCheck("unknown")
    finish = calculated.date()
    if finish <= desired:
        return DesiredCheck("ok", _workdays_between(finish, desired))
    return DesiredCheck("late", _workdays_between(desired, finish))


# --- Срок для коммерческого предложения: расчётная дата ±10 % длительности изготовления ----------------
RANGE_SHARE = 0.10


def _shift_workdays(day: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    for _ in range(abs(days)):
        day += timedelta(days=step)
        while day.weekday() >= 5:
            day += timedelta(days=step)
    return day


@dataclass(frozen=True)
class ProposalRange:
    finish: date  # расчётная дата
    duration: int  # рабочих дней от начала до расчётной даты включительно
    delta: int  # ±10 % длительности, округлено вверх, не меньше 1 дня
    earliest: date
    latest: date

    def against(self, desired: date | None) -> "RangeCheck":
        if desired is None:
            return RangeCheck("none")
        if desired >= self.latest:
            return RangeCheck("ok", _workdays_between(self.latest, desired))
        if desired >= self.earliest:
            return RangeCheck("range", _workdays_between(desired, self.latest))
        return RangeCheck("late", _workdays_between(desired, self.finish))


@dataclass(frozen=True)
class RangeCheck:
    status: str  # ok — успеваем с учётом диапазона; range — желаемая внутри диапазона; late — не успеваем
    workdays: int = 0

    @property
    def label(self) -> str:
        if self.status == "ok":
            return "успеваем" + (f", запас {self.workdays} раб. дн." if self.workdays else "")
        if self.status == "range":
            return f"попадает в диапазон: возможна задержка до {self.workdays} раб. дн."
        if self.status == "late":
            return f"не успеваем: расчётная дата позже желаемой на {self.workdays} раб. дн."
        return ""


def proposal_range(start: date, finish: datetime, not_before: date | None = None) -> ProposalRange:
    """Нижняя граница срока — не раньше даты начала и not_before (дня КП): срок не начинается в прошлом."""
    import math

    end = finish.date()
    duration = _workdays_between(start - timedelta(days=1), end) if end >= start else 0
    delta = max(1, math.ceil(duration * RANGE_SHARE - 1e-9))
    floor = max(d for d in (start, not_before) if d is not None)
    earliest = max(_shift_workdays(end, -delta), min(floor, end))
    return ProposalRange(end, duration, delta, earliest, _shift_workdays(end, delta))
