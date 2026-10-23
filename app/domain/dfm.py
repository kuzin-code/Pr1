"""Проверка технологичности по STEP (локально): правила и предупреждения для технолога.

Правила (пороги — data/seed/dfm_rules.json, параметр «dfm_rules»):
- габарит детали больше рабочей зоны всех допустимых станков (станки с ЧПУ технологии детали, machines.py);
- глубина отверстия ÷ диаметр больше порога (глубокое сверление);
- внутренний радиус меньше порога (нужна фреза малого диаметра).
Чего по данным определить нельзя (нет STEP, тело не одно, у станков не задана рабочая зона) — статус
«не определено», без догадок. Технолог снимает предупреждение только с причиной (в историю).
"""
from dataclasses import dataclass, field
from enum import Enum

from app.domain.models import Dimensions, Machine

# Нейтральные пороги по умолчанию — до согласования с технологом (см. dfm_rules.json).
DEFAULT_RULES = {"max_hole_depth_ratio": 10.0, "min_internal_radius_mm": 1.0}
SHOWN = 3  # сколько примеров перечислять в предупреждении


@dataclass(frozen=True)
class Hole:
    diameter: float  # мм
    depth: float  # мм, вдоль оси

    @property
    def ratio(self) -> float:
        return self.depth / self.diameter if self.diameter > 0 else 0.0


@dataclass
class StepFeatures:
    """Признаки из STEP (app/parsers/step_features.py)."""
    holes: list[Hole] = field(default_factory=list)
    internal_radii: list[float] = field(default_factory=list)  # мм, вогнутые неполные цилиндры

    def to_dict(self) -> dict:
        return {"holes": [[h.diameter, h.depth] for h in self.holes], "internal_radii": self.internal_radii}

    @classmethod
    def from_dict(cls, data: dict | None) -> "StepFeatures | None":
        if data is None:
            return None
        return cls([Hole(float(d), float(t)) for d, t in data.get("holes") or []],
                   [float(r) for r in data.get("internal_radii") or []])


class DfmStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {"ok": "в норме", "warn": "предупреждение", "unknown": "не определено"}[self.value]


@dataclass(frozen=True)
class DfmRules:
    max_hole_depth_ratio: float
    min_internal_radius_mm: float

    @classmethod
    def from_dict(cls, data: dict | None) -> "DfmRules":
        values = {**DEFAULT_RULES, **{k: v for k, v in (data or {}).items() if k in DEFAULT_RULES}}
        return cls(float(values["max_hole_depth_ratio"]), float(values["min_internal_radius_mm"]))


@dataclass(frozen=True)
class DfmCheck:
    code: str  # size / holes / radii
    title: str
    status: DfmStatus
    detail: str

    def to_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "status": self.status.value, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict) -> "DfmCheck":
        return cls(data["code"], data["title"], DfmStatus(data["status"]), data["detail"])


def _num(value: float) -> str:
    return f"{value:g}"


def _dims(dims: Dimensions) -> str:
    return "×".join(_num(v) for v in dims.as_list())


def check_size(dims: Dimensions | None, technology: str, machines: list[Machine]) -> DfmCheck:
    title = "Габарит и рабочая зона станков"
    if dims is None:
        return DfmCheck("size", title, DfmStatus.UNKNOWN, "Габариты не прочитаны")
    candidates = [m for m in machines if m.is_cnc and technology in m.technologies]
    if not candidates:
        return DfmCheck("size", title, DfmStatus.UNKNOWN, "Нет станков с ЧПУ этой технологии")
    fits = [m.code for m in candidates if m.work_zone is not None and dims.fits_in(m.work_zone)]
    if fits:
        return DfmCheck("size", title, DfmStatus.OK, f"Вмещается: {', '.join(fits)}")
    unknown = [m.code for m in candidates if m.work_zone is None]
    if unknown:
        return DfmCheck("size", title, DfmStatus.UNKNOWN,
                        f"Не вмещается в станки с известной зоной; рабочая зона не задана: {', '.join(unknown)}")
    zones = "; ".join(f"{m.code} {_dims(m.work_zone)}" for m in candidates)
    return DfmCheck("size", title, DfmStatus.WARN,
                    f"Габарит {_dims(dims)} мм больше рабочей зоны всех допустимых станков ({zones})")


def check_holes(features: StepFeatures | None, rules: DfmRules) -> DfmCheck:
    title = "Глубина отверстий"
    if features is None:
        return DfmCheck("holes", title, DfmStatus.UNKNOWN, "Не определено: STEP не разобран (нет файла или не одно тело)")
    if not features.holes:
        return DfmCheck("holes", title, DfmStatus.OK, "Отверстий в модели не найдено")
    deep = [h for h in features.holes if h.ratio > rules.max_hole_depth_ratio]
    if not deep:
        worst = max(features.holes, key=lambda h: h.ratio)
        return DfmCheck("holes", title, DfmStatus.OK, f"Отверстий: {len(features.holes)}, наибольшее отношение "
                        f"глубина/диаметр {worst.ratio:.1f} (порог {_num(rules.max_hole_depth_ratio)})")
    shown = "; ".join(f"Ø{_num(h.diameter)} глубиной {_num(h.depth)} мм ({h.ratio:.1f})" for h in deep[:SHOWN])
    more = f" и ещё {len(deep) - SHOWN}" if len(deep) > SHOWN else ""
    return DfmCheck("holes", title, DfmStatus.WARN, f"Глубина/диаметр больше {_num(rules.max_hole_depth_ratio)}: "
                    f"{shown}{more}")


def check_radii(features: StepFeatures | None, rules: DfmRules) -> DfmCheck:
    title = "Внутренние радиусы"
    if features is None:
        return DfmCheck("radii", title, DfmStatus.UNKNOWN, "Не определено: STEP не разобран (нет файла или не одно тело)")
    small = sorted({r for r in features.internal_radii if r < rules.min_internal_radius_mm})
    if not small:
        least = f", наименьший R{_num(min(features.internal_radii))} мм" if features.internal_radii else ""
        return DfmCheck("radii", title, DfmStatus.OK,
                        f"Меньше R{_num(rules.min_internal_radius_mm)} мм нет{least}")
    count = sum(1 for r in features.internal_radii if r < rules.min_internal_radius_mm)
    return DfmCheck("radii", title, DfmStatus.WARN,
                    f"Внутренний радиус меньше R{_num(rules.min_internal_radius_mm)} мм: "
                    f"{', '.join('R' + _num(r) for r in small)} (граней: {count}) — нужна фреза Ø{_num(2 * small[0])} мм")


def check_all(dims: Dimensions | None, technology: str, machines: list[Machine], features: StepFeatures | None,
              rules: DfmRules) -> list[DfmCheck]:
    return [check_size(dims, technology, machines), check_holes(features, rules), check_radii(features, rules)]


def active_warnings(checks: list[DfmCheck], dismissed: dict) -> list[DfmCheck]:
    """Предупреждения, не снятые технологом; снятие действует, пока не изменился текст предупреждения."""
    return [c for c in checks if c.status is DfmStatus.WARN
            and (dismissed.get(c.code) or {}).get("detail") != c.detail]
