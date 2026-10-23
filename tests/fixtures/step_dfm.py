"""Синтетические STEP для проверки технологичности: брусок с отверстиями и внутренними скруглениями."""
import math

from tests.fixtures.samples import _FOOTER, _HEADER


class _Writer:
    def __init__(self):
        self.lines, self.n = [], 1000

    def add(self, text: str) -> int:
        self.n += 1
        self.lines.append(f"#{self.n}={text};")
        return self.n

    def point(self, p) -> int:
        return self.add(f"CARTESIAN_POINT('',({p[0]:.4f},{p[1]:.4f},{p[2]:.4f}))")

    def vertex(self, p) -> int:
        return self.add(f"VERTEX_POINT('',#{self.point(p)})")

    def placement(self, center) -> int:
        z, x = self.add("DIRECTION('',(0.,0.,1.))"), self.add("DIRECTION('',(1.,0.,0.))")
        return self.add(f"AXIS2_PLACEMENT_3D('',#{self.point(center)},#{z},#{x})")

    def arc(self, center, r, a1, a2) -> int:
        """Ребро-дуга окружности в плоскости XY от угла a1 до a2 (градусы); a1 == a2 — полная окружность."""
        circle = self.add(f"CIRCLE('',#{self.placement(center)},{r})")
        p = lambda a: (center[0] + r * math.cos(math.radians(a)), center[1] + r * math.sin(math.radians(a)),  # noqa
                       center[2])
        v1 = self.vertex(p(a1))
        v2 = v1 if a1 == a2 else self.vertex(p(a2))
        return self.add(f"EDGE_CURVE('',#{v1},#{v2},#{circle},.T.)")

    def face(self, axis_point, r, edges, concave=True) -> int:
        surface = self.add(f"CYLINDRICAL_SURFACE('',#{self.placement(axis_point)},{r})")
        oriented = ",".join(f"#{self.add(f'ORIENTED_EDGE({chr(39)}{chr(39)},*,*,#{e},.T.)')}" for e in edges)
        loop = self.add(f"EDGE_LOOP('',({oriented}))")
        bound = self.add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
        return self.add(f"ADVANCED_FACE('',(#{bound}),#{surface},{'.F.' if concave else '.T.'})")


def make_dfm_step(a=100.0, b=60.0, c=20.0, holes=(), fillets=(), bosses=(), split=False, bodies=1,
                  bottom_holes=()) -> str:
    """holes: (x, y, Ø, глубина) от верхней грани; bottom_holes — от нижней; fillets: (x, y, R, высота) — четверть цилиндра;
    bosses: (x, y, Ø, высота) — выпуклый цилиндр (не отверстие); split — отверстие из двух половин."""
    w = _Writer()
    for x in (0, a):
        for y in (0, b):
            for z in (0, c):
                w.vertex((x, y, z))
    for x, y, d, depth in holes:
        r = d / 2
        top, bottom = (x, y, c), (x, y, c - depth)
        if split:
            for a1, a2 in ((0, 180), (180, 360)):
                w.face(bottom, r, [w.arc(top, r, a1, a2), w.arc(bottom, r, a1, a2)])
        else:
            w.face(bottom, r, [w.arc(top, r, 0, 0), w.arc(bottom, r, 0, 0)])
    for x, y, d, depth in bottom_holes:
        w.face((x, y, 0), d / 2, [w.arc((x, y, 0), d / 2, 0, 0), w.arc((x, y, depth), d / 2, 0, 0)])
    for x, y, r, height in fillets:
        w.face((x, y, 0), r, [w.arc((x, y, 0), r, 0, 90), w.arc((x, y, height), r, 0, 90)])
    for x, y, d, height in bosses:
        w.face((x, y, 0), d / 2, [w.arc((x, y, 0), d / 2, 0, 0), w.arc((x, y, height), d / 2, 0, 0)], concave=False)
    body = [f"#{10 + i}=MANIFOLD_SOLID_BREP('body{i}',#50);" for i in range(bodies)]
    body.append("#60=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );")
    return _HEADER + "\n".join(body + w.lines) + "\n" + _FOOTER
