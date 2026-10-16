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
