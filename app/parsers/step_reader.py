"""Чтение габаритов и объёма из STEP (ISO 10303-21), ФТ-03.

Габариты — ограничивающий параллелепипед по вершинам (VERTEX_POINT) и окружностям (CIRCLE),
поэтому цилиндрические поверхности учитываются по диаметру. Если тел больше одного или файл
не читается, габариты не рассчитываются: их вводит технолог (О-1, О-3).
"""
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.models import Dimensions

_NUM = r"[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?\.?"
_POINT = re.compile(r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)")
_DIRECTION = re.compile(r"#(\d+)\s*=\s*DIRECTION\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)")
_VERTEX = re.compile(r"VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*\)")
_AXIS = re.compile(
    r"#(\d+)\s*=\s*AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*(#\d+|\$)\s*,\s*(#\d+|\$)\s*\)")
_CIRCLE = re.compile(rf"=\s*CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*({_NUM})\s*\)")
_BODY = re.compile(r"=\s*(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(")
_VOLUME = re.compile(rf"VOLUME_MEASURE\s*\(\s*({_NUM})\s*\)")
_MM = re.compile(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)")
_METRE = re.compile(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.\s*\)")


@dataclass
class StepData:
    dims: Dimensions | None = None
    volume: float | None = None  # мм³
    bodies: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def needs_manual(self) -> bool:
        return self.dims is None


def _floats(text: str) -> tuple[float, ...]:
    return tuple(float(v.strip().rstrip(".") or 0) for v in text.split(","))


def _unit_scale(text: str) -> float:
    if _MM.search(text):
        return 1.0
    if _METRE.search(text):
        return 1000.0
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text, re.I):
        return 25.4
    return 1.0


_EDGE = re.compile(r"=\s*EDGE_CURVE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)\s*,\s*#(\d+)\s*,\s*\.([TF])\.\s*\)")
_TWO_PI = 2 * math.pi


def _unit(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / length for c in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _frame(axis, points, directions):
    """Центр и базис окружности: n — ось, x — опорное направление, y = n × x."""
    center = points[axis.group(2)]
    n = _unit(directions.get(axis.group(3).lstrip("#"), (0.0, 0.0, 1.0)))
    ref = directions.get(axis.group(4).lstrip("#"))
    if ref is None or abs(sum(a * b for a, b in zip(_unit(ref), n))) > 0.999:
        ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    r = _unit(ref)
    dot = sum(a * b for a, b in zip(r, n))
    x = _unit([a - dot * b for a, b in zip(r, n)])
    return center, x, _unit(_cross(n, x))


def _circle_extents(text, points, directions):
    """Крайние точки окружностей; для дуг (EDGE_CURVE) — только в пределах угла дуги."""
    axes = {m.group(1): m for m in _AXIS.finditer(text)}
    circles = {}
    for m in re.finditer(rf"#(\d+)\s*=\s*CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*({_NUM})\s*\)", text):
        axis = axes.get(m.group(2))
        if axis is not None and axis.group(2) in points:
            circles[m.group(1)] = (*_frame(axis, points, directions), float(m.group(3).rstrip(".")))
    vertices = {m.group(1): points.get(m.group(2)) for m in
                re.finditer(r"#(\d+)\s*=\s*VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*\)", text)}
    arcs = {}
    for m in _EDGE.finditer(text):
        if m.group(3) in circles:
            arcs.setdefault(m.group(3), []).append((vertices.get(m.group(1)), vertices.get(m.group(2)), m.group(4) == "T"))
    for cid, (center, x, y, radius) in circles.items():
        def angle(p, center=center, x=x, y=y):
            d = [a - b for a, b in zip(p, center)]
            return math.atan2(sum(a * b for a, b in zip(d, y)), sum(a * b for a, b in zip(d, x)))

        spans = []
        for v1, v2, same_sense in arcs.get(cid, [(None, None, True)]):
            if v1 is None or v2 is None or max(abs(a - b) for a, b in zip(v1, v2)) < 1e-6:
                spans.append((0.0, _TWO_PI))  # полная окружность
                continue
            a1, a2 = (angle(v1), angle(v2)) if same_sense else (angle(v2), angle(v1))
            spans.append((a1, (a2 - a1) % _TWO_PI))
        for i in range(3):
            base = math.atan2(y[i], x[i])
            for phi in (base, base + math.pi):
                if any((phi - start) % _TWO_PI <= span + 1e-9 for start, span in spans):
                    yield tuple(c + radius * (math.cos(phi) * xi + math.sin(phi) * yi)
                                for c, xi, yi in zip(center, x, y))


def parse_step_text(text: str) -> StepData:
    data = StepData()
    if "ISO-10303-21" not in text or "DATA;" not in text:
        data.errors.append("Файл STEP не читается или повреждён: введите габариты вручную")
        return data
    data.bodies = len(_BODY.findall(text))
    scale = _unit_scale(text)
    volume = _VOLUME.search(text)
    if volume:
        data.volume = float(volume.group(1).rstrip(".")) * scale ** 3
    if data.bodies > 1:
        data.errors.append(f"STEP содержит несколько тел ({data.bodies}): введите габариты вручную")
        return data
    if data.bodies == 0:
        data.errors.append("В STEP не найдено твёрдое тело: введите габариты вручную")
        return data

    points = {m.group(1): _floats(m.group(2)) for m in _POINT.finditer(text)}
    directions = {m.group(1): _floats(m.group(2)) for m in _DIRECTION.finditer(text)}
    cloud = [points[v] for v in _VERTEX.findall(text) if v in points]
    cloud += list(_circle_extents(text, points, directions))
    cloud = [p for p in cloud if len(p) == 3]
    if not cloud:
        data.errors.append("В STEP не найдена геометрия: введите габариты вручную")
        return data
    size = [round((max(p[i] for p in cloud) - min(p[i] for p in cloud)) * scale, 2) for i in range(3)]
    try:
        data.dims = Dimensions(*size)
    except ValueError:
        data.errors.append("Габариты по STEP не определены (плоская геометрия): введите вручную")
    return data


def read_step(path: Path | str) -> StepData:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return StepData(errors=["Файл STEP не найден или не читается: введите габариты вручную"])
    return parse_step_text(text)
