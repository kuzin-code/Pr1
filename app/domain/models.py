"""Структуры данных предметной области (ТЗ 1.0, раздел 4.3)."""
import math
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.downtime import counts_in_k
from app.domain.enums import CountMode, Stage, StageGroup


@dataclass(frozen=True)
class Dimensions:
    """Габариты, мм. Сравнение ведётся по упорядоченным размерам (ориентация детали не важна)."""

    a: float
    b: float
    c: float

    def __post_init__(self):
        if min(self.a, self.b, self.c) <= 0:
            raise ValueError("Габариты должны быть положительными")

    def sorted(self) -> tuple[float, float, float]:
        return tuple(sorted((self.a, self.b, self.c), reverse=True))

    def fits_in(self, zone: "Dimensions") -> bool:
        return all(p <= z for p, z in zip(self.sorted(), zone.sorted()))

    def max_deviation(self, other: "Dimensions") -> float:
        return max(abs(p - q) for p, q in zip(self.sorted(), other.sorted()))

    def as_list(self) -> list[float]:
        return [self.a, self.b, self.c]

    @classmethod
    def from_list(cls, values) -> "Dimensions | None":
        if not values:
            return None
        return cls(*(float(v) for v in values))


@dataclass(frozen=True)
class StageNorm:
    """Этап маршрута: время на единицу счёта и способ счёта (Д-4)."""

    stage: Stage
    hours: float
    count_mode: CountMode = CountMode.PER_PIECE

    def __post_init__(self):
        if not math.isfinite(self.hours):
            raise ValueError("Время этапа должно быть числом")
        if self.hours < 0:
            raise ValueError("Время этапа не может быть отрицательным")
        if self.stage is Stage.SETUP and self.count_mode is CountMode.PER_PIECE:
            # Наладка считается один раз на партию (ФТ-11).
            object.__setattr__(self, "count_mode", CountMode.PER_BATCH)

    def batch_hours(self, quantity: int) -> float:
        if self.count_mode is CountMode.PER_PIECE:
            return self.hours * quantity
        return self.hours

    def to_dict(self) -> dict:
        return {"stage": self.stage.value, "hours": self.hours, "count_mode": self.count_mode.value}

    @classmethod
    def from_dict(cls, data: dict) -> "StageNorm":
        return cls(Stage(data["stage"]), float(data["hours"]), CountMode(data["count_mode"]))


@dataclass(frozen=True)
class Measurement:
    """Замер: один введённый факт по партии детали и группе этапов (ФТ-12)."""

    group: StageGroup
    calc_hours: float
    actual_hours: float
    position_id: int | None = None
    at: datetime | None = None
    reason: str | None = None  # причина отклонения при «Закончил» (downtime.Reason)

    @property
    def ratio(self) -> float:
        return self.actual_hours / self.calc_hours

    @property
    def in_k(self) -> bool:
        """Замер с причиной-потерей (ремонт, ждал заготовку, брак, другое) в k не идёт."""
        return counts_in_k(self.reason)

    def to_dict(self) -> dict:
        return {
            "group": self.group.value,
            "calc_hours": self.calc_hours,
            "actual_hours": self.actual_hours,
            "position_id": self.position_id,
            "at": self.at.isoformat() if self.at else None,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Measurement":
        at = datetime.fromisoformat(data["at"]) if data.get("at") else None
        return cls(StageGroup(data["group"]), float(data["calc_hours"]), float(data["actual_hours"]),
                   data.get("position_id"), at, data.get("reason"))


@dataclass
class CalibrationEntry:
    """Запись журнала калибровки (Д-4)."""

    part_code: str
    name: str
    machine_code: str
    material: str
    dims: Dimensions
    tolerance_grade: int
    complexity: int
    stages: list[StageNorm]
    measurements: list[Measurement] = field(default_factory=list)
    technology: str = "milling"


@dataclass(frozen=True)
class Machine:
    """Оборудование (Д-5)."""

    code: str
    model: str
    kind: str
    is_cnc: bool
    work_zone: Dimensions | None
    technologies: frozenset[str]
    controller: str = ""
    accuracy: str = ""
    power: str = ""
    hours_per_day: float = 6


@dataclass(frozen=True)
class PartFeatures:
    """Признаки новой детали для подбора аналога и станка."""

    dims: Dimensions
    material: str
    tolerance_grade: int
    complexity: int
    technology: str = "milling"


@dataclass(frozen=True)
class Rates:
    """Ставки, руб/ч (Д-6)."""

    machine: float
    bench: float

    def to_dict(self) -> dict:
        return {"machine": self.machine, "bench": self.bench}
