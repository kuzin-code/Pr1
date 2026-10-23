"""Признаки технологичности из STEP (ISO 10303-21): отверстия и внутренние радиусы по цилиндрическим граням.

Определяется только то, что однозначно видно в B-rep:
- вогнутая цилиндрическая грань (ADVANCED_FACE с флагом .F.: нормаль из материала смотрит на ось) —
  отверстие или внутреннее скругление; выпуклая (.T.) — наружная поверхность, не учитывается;
- соосные вогнутые грани одного радиуса объединяются (CAD часто делит отверстие на две половины):
  охват по углу 360° — отверстие (диаметр; глубина — размах вершин граней вдоль оси), меньше — внутренний радиус;
- если тело не одно или файл не разбирается — признаки не определены (None): ничего не угадываем.
"""
import math
import re
from pathlib import Path

from app.domain.dfm import Hole, StepFeatures
from app.parsers.step_reader import _BODY, _floats, _unit, _unit_scale

_ENTITY = re.compile(r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*\((.*?)\)\s*;", re.S)
_REF = re.compile(r"#(\d+)")
_NUMBER = re.compile(r"([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)\.?\s*$")
_TWO_PI = 2 * math.pi
_FULL = math.radians(359)  # охват не меньше — полный цилиндр (отверстие)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


class _Model:
    """Сущности файла: номер → (тип, аргументы)."""

    def __init__(self, text: str):
        self.items = {m.group(1): (m.group(2), m.group(3)) for m in _ENTITY.finditer(text)}

    def kind(self, eid: str) -> str:
        return self.items.get(eid, ("", ""))[0]

    def args(self, eid: str) -> str:
        return self.items.get(eid, ("", ""))[1]

    def refs(self, eid: str) -> list[str]:
        return _REF.findall(self.args(eid))

    def flag(self, eid: str) -> bool:
        return self.args(eid).rstrip().endswith(".T.")

    def number(self, eid: str) -> float | None:
        m = _NUMBER.search(self.args(eid))
        return float(m.group(1)) if m else None

    def point(self, eid: str):
        if self.kind(eid) == "VERTEX_POINT":
            return self.point(self.refs(eid)[0])
        if self.kind(eid) in ("CARTESIAN_POINT", "DIRECTION"):
            m = re.search(r"\(([^()]*)\)\s*$", self.args(eid))
            values = _floats(m.group(1)) if m else ()
            return values if len(values) == 3 else None
        return None

    def placement(self, eid: str):
        """Центр, ось n и базис (x, y) размещения AXIS2_PLACEMENT_3D."""
        refs = self.refs(eid)
        center = self.point(refs[0]) if refs else None
        axis = self.point(refs[1]) if len(refs) > 1 else None
        n = _unit(axis or (0.0, 0.0, 1.0))
        ref = self.point(refs[2]) if len(refs) > 2 else None
        if ref is None or abs(_dot(_unit(ref), n)) > 0.999:
            ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
        r = _unit(ref)
        x = _unit([a - _dot(r, n) * b for a, b in zip(r, n)])
        return center, n, x, _unit(_cross(n, x))

    def face_edges(self, face: str) -> list[tuple[str, str, str, bool]]:
        """Рёбра грани: (вершина 1, вершина 2, кривая, ребро идёт по направлению кривой)."""
        edges = []
        for bound in self.refs(face):
            if self.kind(bound) not in ("FACE_OUTER_BOUND", "FACE_BOUND"):
                continue
            for oriented in self.refs(self.refs(bound)[0]):
                if self.kind(oriented) != "ORIENTED_EDGE":
                    continue
                edge = self.refs(oriented)[0]
                if self.kind(edge) == "EDGE_CURVE" and len(self.refs(edge)) >= 3:
                    v1, v2, curve = self.refs(edge)[:3]
                    edges.append((v1, v2, curve, self.flag(edge)))
        return edges

    def arc_span(self, v1: str, v2: str, curve: str, sense: bool) -> float:
        """Угол дуги окружности ребра (полная окружность — 2π)."""
        p1, p2 = self.point(v1), self.point(v2)
        refs = self.refs(curve)
        if p1 is None or p2 is None or not refs:
            return 0.0
        if max(abs(a - b) for a, b in zip(p1, p2)) < 1e-6:
            return _TWO_PI
        center, _, x, y = self.placement(refs[0])
        if center is None:
            return 0.0

        def angle(p):
            d = [a - b for a, b in zip(p, center)]
            return math.atan2(_dot(d, y), _dot(d, x))

        a1, a2 = (angle(p1), angle(p2)) if sense else (angle(p2), angle(p1))
        return (a2 - a1) % _TWO_PI


def _axis_key(center, n, radius: float):
    """Ось и радиус для объединения соосных граней: направление без знака, точка оси, радиус;
    и направление со знаком ключа — общая координата вдоль оси для всех граней этой оси."""
    sign = next((1 if c > 0 else -1 for c in n if abs(c) > 1e-6), 1)
    n = [c * sign for c in n]
    foot = [c - _dot(center, n) * k for c, k in zip(center, n)]
    return tuple(round(c, 4) for c in n) + tuple(round(c, 2) for c in foot) + (round(radius, 3),), n


def _clusters(faces):
    """Соосные грани одного радиуса, касающиеся или перекрывающиеся вдоль оси, — один элемент
    (половинки одного отверстия); разнесённые по оси — разные отверстия."""
    result = []
    for span, lo, hi, radius in sorted(faces, key=lambda f: f[1]):
        if result and lo <= result[-1][2] + 0.01:
            s, l, h, r = result[-1]
            result[-1] = (min(s + span, _TWO_PI), l, max(h, hi), r)
        else:
            result.append((span, lo, hi, radius))
    return result


def parse_features(text: str) -> StepFeatures | None:
    """Отверстия и внутренние радиусы; None — не определено (тело не одно, файл не разбирается)."""
    if "ISO-10303-21" not in text or "DATA;" not in text or len(_BODY.findall(text)) != 1:
        return None
    scale = _unit_scale(text)
    model = _Model(text)
    groups: dict[tuple, list[tuple[float, float, float, float]]] = {}  # ось → грани (охват, t min, t max, r)
    for eid, (kind, _) in model.items.items():
        if kind != "ADVANCED_FACE" or model.flag(eid):
            continue  # нужна вогнутая грань (.F.)
        surface = next((r for r in model.refs(eid) if model.kind(r) == "CYLINDRICAL_SURFACE"), None)
        radius = model.number(surface) if surface else None
        if surface is None or not radius or not model.refs(surface):
            continue
        center, n, _, _ = model.placement(model.refs(surface)[0])
        if center is None:
            continue
        edges = model.face_edges(eid)
        span = max((model.arc_span(*e) for e in edges if model.kind(e[2]) == "CIRCLE"), default=0.0)
        key, axis = _axis_key(center, n, radius)
        ts = [_dot(p, axis) for v1, v2, _, _ in edges for p in (model.point(v1), model.point(v2)) if p is not None]
        if ts:
            groups.setdefault(key, []).append((span, min(ts), max(ts), radius))
    features = StepFeatures()
    for faces in groups.values():
        for span, lo, hi, radius in _clusters(faces):
            radius = round(radius * scale, 3)
            if span >= _FULL:
                depth = (hi - lo) * scale
                if depth > 0:
                    features.holes.append(Hole(round(2 * radius, 3), round(depth, 3)))
            else:
                features.internal_radii.append(radius)
    features.holes.sort(key=lambda h: (-h.ratio, h.diameter))
    features.internal_radii.sort()
    return features


def read_features(path: Path | str) -> StepFeatures | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_features(text)
