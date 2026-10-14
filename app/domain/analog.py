"""Подбор аналога из журнала калибровки (ФТ-07)."""
from dataclasses import dataclass

from app.domain.models import CalibrationEntry, PartFeatures

DIM_TOLERANCE_MM = 10.0
GRADE_TOLERANCE = 1  # «подобные» квалитеты: отличие не более чем на 1 квалитет
COMPLEXITY_TOLERANCE = 0


@dataclass(frozen=True)
class AnalogMatch:
    entry: CalibrationEntry
    max_dim_deviation: float
    grade_difference: int
    reasons: tuple[str, ...]


def normalize_material(material: str) -> str:
    return "".join(material.split()).casefold()


def _match(part: PartFeatures, entry: CalibrationEntry) -> AnalogMatch | None:
    if entry.technology != part.technology:
        return None
    if abs(entry.complexity - part.complexity) > COMPLEXITY_TOLERANCE:
        return None
    grade_diff = abs(entry.tolerance_grade - part.tolerance_grade)
    if grade_diff > GRADE_TOLERANCE:
        return None
    if normalize_material(entry.material) != normalize_material(part.material):
        return None
    deviation = part.dims.max_deviation(entry.dims)
    if deviation > DIM_TOLERANCE_MM:
        return None
    reasons = (
        f"сложность {entry.complexity} = {part.complexity}",
        f"квалитет IT{entry.tolerance_grade} ~ IT{part.tolerance_grade}",
        f"габариты: отклонение до {deviation:g} мм (допуск ±{DIM_TOLERANCE_MM:g})",
        f"материал {entry.material}",
    )
    return AnalogMatch(entry, deviation, grade_diff, reasons)


def find_analogs(part: PartFeatures, journal: list[CalibrationEntry]) -> list[AnalogMatch]:
    """Подходящие аналоги, ближайшие первыми. Выбор подтверждает технолог."""
    matches = [m for m in (_match(part, e) for e in journal) if m is not None]
    return sorted(matches, key=lambda m: (m.max_dim_deviation, m.grade_difference, m.entry.part_code))
