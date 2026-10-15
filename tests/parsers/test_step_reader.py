"""ФТ-03, О-1, О-3: чтение габаритов из STEP."""
import pytest

from app.parsers.step_reader import parse_step_text, read_step
from tests.fixtures.samples import make_box_step, make_cylinder_step


def test_box_dimensions_from_vertices():
    data = parse_step_text(make_box_step(100, 60, 20))
    assert data.bodies == 1
    assert data.dims.sorted() == pytest.approx((100, 60, 20))
    assert not data.needs_manual
    assert data.errors == []


def test_metre_units_converted_to_mm():
    data = parse_step_text(make_box_step(0.1, 0.06, 0.02, unit="METRE"))
    assert data.dims.sorted() == pytest.approx((100, 60, 20))


def test_cylinder_diameter_from_circles():
    data = parse_step_text(make_cylinder_step(radius=25, length=80))
    assert data.dims.sorted() == pytest.approx((80, 50, 50))


def test_several_bodies_requires_manual_input():
    data = parse_step_text(make_box_step(bodies=2))
    assert data.bodies == 2
    assert data.dims is None
    assert data.needs_manual
    assert "несколько тел" in data.errors[0]


def test_corrupted_file_requires_manual_input():
    data = parse_step_text("это не STEP")
    assert data.dims is None and data.needs_manual
    assert "не читается" in data.errors[0]


def test_volume_read_when_present():
    text = make_box_step().replace("ENDSEC;\nEND", "#900=MEASURE_REPRESENTATION_ITEM('volume',VOLUME_MEASURE(120000.),#60);\nENDSEC;\nEND")
    assert parse_step_text(text).volume == pytest.approx(120000)


def test_volume_absent_is_none():
    assert parse_step_text(make_box_step()).volume is None


def test_read_step_from_file(tmp_path):
    path = tmp_path / "part.step"
    path.write_text(make_box_step(), encoding="utf-8")
    assert read_step(path).dims is not None


def test_read_step_missing_or_binary_file(tmp_path):
    path = tmp_path / "bad.step"
    path.write_bytes(b"\x00\xff\x00garbage")
    assert read_step(path).needs_manual
    assert read_step(tmp_path / "absent.step").needs_manual


def test_arc_counts_only_its_angle():
    from tests.fixtures.samples import make_arc_step
    assert parse_step_text(make_arc_step(35, 10)).dims.sorted() == pytest.approx((70, 35, 10))
    assert parse_step_text(make_arc_step(35, 10, ".F.")).dims.sorted() == pytest.approx((70, 35, 10))
