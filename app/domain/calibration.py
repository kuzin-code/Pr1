"""Коэффициент калибровки k (ФТ-12)."""
from dataclasses import dataclass, replace
from datetime import datetime
from statistics import fmean

from app.domain.enums import StageGroup
from app.domain.models import CalibrationEntry, Measurement

MIN_MEASUREMENTS = 5
K_GROUPS = (StageGroup.MACHINE, StageGroup.BENCH)


@dataclass(frozen=True)
class KValue:
    value: float
    count: int

    @property
    def approximate(self) -> bool:
        return self.count < MIN_MEASUREMENTS

    def to_dict(self) -> dict:
        return {"value": self.value, "count": self.count}


NO_ANALOG_K = KValue(1.0, 0)


def k_for(entry: CalibrationEntry | None, group: StageGroup) -> KValue:
    """k аналога для группы — среднее отношений факт ÷ расчёт по всем замерам."""
    if entry is None:
        return NO_ANALOG_K
    # Нулевой факт — ошибочная отметка, а не замер: в k не учитывается.
    ratios = [m.ratio for m in entry.measurements if m.group is group and m.actual_hours > 0]
    if not ratios:
        return NO_ANALOG_K
    return KValue(fmean(ratios), len(ratios))


def add_measurement(entry: CalibrationEntry, group: StageGroup, calc_hours: float,
                    actual_hours: float, position_id: int | None = None,
                    at: datetime | None = None) -> CalibrationEntry:
    """Новая версия записи журнала с добавленным замером."""
    if group not in K_GROUPS:
        raise ValueError("k определяется только для станочной и слесарно-финишной групп")
    if calc_hours <= 0 or actual_hours <= 0:
        raise ValueError("Расчётное и фактическое время замера должны быть больше нуля")
    measurement = Measurement(group, calc_hours, actual_hours, position_id, at)
    return replace(entry, measurements=[*entry.measurements, measurement])
