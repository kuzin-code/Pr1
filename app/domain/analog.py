"""Подбор аналога из журнала калибровки (ФТ-07)."""
from dataclasses import dataclass

from app.domain.enums import Stage
from app.domain.materials import machinability, material_group
from app.domain.models import CalibrationEntry, PartFeatures, StageNorm

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


# --- Автоматический выбор аналога: строгий, иначе ближайший той же технологии ---

@dataclass(frozen=True)
class AnalogChoice:
    entry: CalibrationEntry
    approximate: bool  # True — не прошёл строгие условия ФТ-07, время пересчитано
    route: list[StageNorm]
    reasons: tuple[str, ...]


# Этапы, время которых пересчитывается при приближённом аналоге.
_SIZE_STAGES = {Stage.CUTTING, Stage.MACHINING, Stage.LOCKSMITH, Stage.FINISHING}
_COMPLEXITY_STAGES = {Stage.PROGRAMMING, Stage.MACHINING, Stage.LOCKSMITH, Stage.FINISHING}
COMPLEXITY_STEP = 0.25


def _volume(dims) -> float:
    a, b, c = dims.sorted()
    return a * b * c


def _relative_deviation(part, entry) -> float:
    return max(abs(p - q) / max(p, q) for p, q in zip(part.dims.sorted(), entry.dims.sorted()))


def _score(part: PartFeatures, entry: CalibrationEntry) -> float:
    same_group = material_group(part.material) == material_group(entry.material)
    return (abs(part.complexity - entry.complexity) + 0.5 * abs(part.tolerance_grade - entry.tolerance_grade)
            + 2 * _relative_deviation(part, entry) + (0 if same_group else 1))


def scale_route(part: PartFeatures, entry: CalibrationEntry) -> list[StageNorm]:
    """Время аналога, пересчитанное на габарит, материал и сложность новой детали."""
    size = (_volume(part.dims) / _volume(entry.dims)) ** (2 / 3)
    material = machinability(part.material) / machinability(entry.material)
    complexity = max(0.5, 1 + COMPLEXITY_STEP * (part.complexity - entry.complexity))
    route = []
    for norm in entry.stages:
        factor = 1.0
        if norm.stage in _SIZE_STAGES:
            factor *= size
        if norm.stage is Stage.MACHINING:
            factor *= material
        if norm.stage in _COMPLEXITY_STAGES:
            factor *= complexity
        hours = round(norm.hours * factor, 2)
        route.append(StageNorm(norm.stage, max(hours, 0.01) if norm.hours else 0.0, norm.count_mode))
    return route


def evaluate_analog(part: PartFeatures, entry: CalibrationEntry) -> AnalogChoice:
    """Строгий аналог — время журнала как есть; иначе время пересчитывается."""
    match = _match(part, entry)
    if match is not None:
        return AnalogChoice(entry, False, list(entry.stages), match.reasons)
    reasons = (
        f"та же технология ({part.technology})",
        f"сложность {entry.complexity} (у детали {part.complexity})",
        f"IT{entry.tolerance_grade} (у детали IT{part.tolerance_grade})",
        f"габариты {'×'.join(f'{d:g}' for d in entry.dims.sorted())} мм",
        f"материал {entry.material} → {part.material or 'не указан'}",
        "время пересчитано на габарит, материал и сложность",
    )
    return AnalogChoice(entry, True, scale_route(part, entry), reasons)


def pick_analog(part: PartFeatures, journal: list[CalibrationEntry]) -> AnalogChoice | None:
    """Аналог выбирает система; в первую очередь — по технологии обработки."""
    strict = find_analogs(part, journal)
    if strict:
        return evaluate_analog(part, strict[0].entry)
    same_tech = [e for e in journal if e.technology == part.technology]
    if not same_tech:
        return None
    return evaluate_analog(part, min(same_tech, key=lambda e: (_score(part, e), e.part_code)))


def same_part(journal: list[CalibrationEntry], designation: str, drawing_designation: str | None = None
              ) -> AnalogChoice | None:
    """Деталь с тем же обозначением уже есть в журнале — она и есть аналог (время журнала как есть)."""
    import re

    code = re.sub(r"\s*\(\d+\)$", "", designation).strip().casefold()
    number = (drawing_designation or "").strip().casefold()
    for entry in journal:
        part = entry.part_code.casefold()
        if part == code or (number and (part == number or part.startswith(number + " "))):
            return AnalogChoice(entry, False, list(entry.stages), ("та же деталь в журнале калибровки",))
    return None
