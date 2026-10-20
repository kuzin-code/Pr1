"""Расчётное и плановое время этапов (ФТ-11): плановое = расчётное × k группы."""
from dataclasses import dataclass

from app.domain.enums import CountMode, Stage, StageGroup
from app.domain.models import StageNorm


@dataclass(frozen=True)
class StageTime:
    stage: Stage
    unit_hours: float
    count_mode: CountMode
    calc_hours: float  # на партию, до применения k
    k: float
    planned_hours: float  # на партию
    machine: str | None = None  # второй станок техпроцесса; None — основной станок позиции
    technology: str | None = None

    @property
    def group(self) -> StageGroup:
        return self.stage.group

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "unit_hours": self.unit_hours,
            "count_mode": self.count_mode.value,
            "calc_hours": self.calc_hours,
            "k": self.k,
            "planned_hours": self.planned_hours,
            **({"machine": self.machine, "technology": self.technology} if self.machine else {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StageTime":
        return cls(Stage(d["stage"]), d["unit_hours"], CountMode(d["count_mode"]),
                   d["calc_hours"], d["k"], d["planned_hours"], d.get("machine"), d.get("technology"))


def compute_stage_times(route: list[StageNorm], quantity: int, k_machine: float,
                        k_bench: float) -> list[StageTime]:
    if quantity < 1:
        raise ValueError("Количество должно быть не меньше 1")
    k_by_group = {StageGroup.MACHINE: k_machine, StageGroup.BENCH: k_bench, StageGroup.OTHER: 1.0}
    result = []
    for norm in route:
        calc = norm.batch_hours(quantity)
        k = k_by_group[norm.stage.group]
        result.append(StageTime(norm.stage, norm.hours, norm.count_mode, calc, k, calc * k))
    return result


def group_calc_hours(times: list[StageTime], group: StageGroup) -> float:
    return sum(t.calc_hours for t in times if t.group is group)


def group_planned_hours(times: list[StageTime], group: StageGroup) -> float:
    return sum(t.planned_hours for t in times if t.group is group)


def stage_planned_hours(times: list[StageTime], stage: Stage) -> float:
    return sum(t.planned_hours for t in times if t.stage is stage)


def primary_machine_hours(times: list[StageTime]) -> float:
    """Наладка и обработка на основном станке позиции."""
    return sum(t.planned_hours for t in times if t.group is StageGroup.MACHINE and not t.machine)


def extra_machine_hours(times: list[StageTime]) -> list[tuple[str, float]]:
    """Второй (и следующий) станок техпроцесса: часы по станкам в порядке маршрута."""
    hours: dict[str, float] = {}
    for t in times:
        if t.machine:
            hours[t.machine] = hours.get(t.machine, 0.0) + t.planned_hours
    return list(hours.items())
