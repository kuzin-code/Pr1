"""Генераторы тестовых STEP и PDF."""


def make_pdf(lines: list[str]) -> bytes:
    """Минимальный корректный PDF с текстом (ASCII, шрифт Helvetica)."""
    ops = ["BT", "/F1 12 Tf", "14 TL", "50 780 Td"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({safe}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


_HEADER = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('test'),'2;1');
FILE_NAME('part.step','2026-10-14',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
"""
_FOOTER = "ENDSEC;\nEND-ISO-10303-21;\n"


def make_box_step(a=100.0, b=60.0, c=20.0, bodies=1, unit="MILLI", offset=(5.0, -3.0, 0.0)) -> str:
    """STEP с телом-параллелепипедом: 8 вершин + точка системы координат в начале."""
    lines = []
    n = 100
    ox, oy, oz = offset
    vertex_ids = []
    for x in (0, a):
        for y in (0, b):
            for z in (0, c):
                lines.append(f"#{n}=CARTESIAN_POINT('',({ox + x:.3f},{oy + y:.3f},{oz + z:.3f}));")
                lines.append(f"#{n + 1}=VERTEX_POINT('',#{n});")
                vertex_ids.append(n + 1)
                n += 2
    lines.append(f"#{n}=CARTESIAN_POINT('',(-500.,-500.,-500.));")  # не вершина: не влияет
    for i in range(bodies):
        lines.append(f"#{10 + i}=MANIFOLD_SOLID_BREP('body{i}',#50);")
    unit_expr = "SI_UNIT(.MILLI.,.METRE.)" if unit == "MILLI" else "SI_UNIT($,.METRE.)"
    lines.append(f"#60=( LENGTH_UNIT() NAMED_UNIT(*) {unit_expr} );")
    return _HEADER + "\n".join(lines) + "\n" + _FOOTER


def make_cylinder_step(radius=25.0, length=80.0) -> str:
    """Цилиндр вдоль Z: вершины только на шве, диаметр берётся из CIRCLE."""
    body = f"""#1=MANIFOLD_SOLID_BREP('shaft',#2);
#10=CARTESIAN_POINT('',({radius},0.,0.));
#11=VERTEX_POINT('',#10);
#12=CARTESIAN_POINT('',({radius},0.,{length}));
#13=VERTEX_POINT('',#12);
#20=CARTESIAN_POINT('',(0.,0.,0.));
#21=DIRECTION('',(0.,0.,1.));
#22=DIRECTION('',(1.,0.,0.));
#23=AXIS2_PLACEMENT_3D('',#20,#21,#22);
#24=CIRCLE('',#23,{radius});
#30=CARTESIAN_POINT('',(0.,0.,{length}));
#31=AXIS2_PLACEMENT_3D('',#30,#21,#22);
#32=CIRCLE('',#31,{radius});
#40=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
"""
    return _HEADER + body + _FOOTER


def make_arc_step(radius=35.0, height=10.0, same_sense=".T.") -> str:
    """Полукруглая пластина: дуга R от (R,0) до (-R,0) — габарит 2R × R × h, а не 2R × 2R × h."""
    lines = ["#1=MANIFOLD_SOLID_BREP('plate',#2);",
             "#20=DIRECTION('',(0.,0.,1.));", "#21=DIRECTION('',(1.,0.,0.));",
             "#40=( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );"]
    n = 100
    for z in (0.0, height):
        a, b = n, n + 2
        lines += [f"#{a}=CARTESIAN_POINT('',({radius},0.,{z}));", f"#{a + 1}=VERTEX_POINT('',#{a});",
                  f"#{b}=CARTESIAN_POINT('',({-radius},0.,{z}));", f"#{b + 1}=VERTEX_POINT('',#{b});",
                  f"#{n + 4}=CARTESIAN_POINT('',(0.,0.,{z}));",
                  f"#{n + 5}=AXIS2_PLACEMENT_3D('',#{n + 4},#20,#21);",
                  f"#{n + 6}=CIRCLE('',#{n + 5},{radius});",
                  f"#{n + 7}=EDGE_CURVE('',#{a + 1},#{b + 1},#{n + 6},{same_sense});"]
        n += 10
    return _HEADER + "\n".join(lines) + "\n" + _FOOTER
