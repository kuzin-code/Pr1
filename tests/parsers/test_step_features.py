"""Признаки технологичности из STEP: отверстия, внутренние радиусы; синтетика и реальные STEP журнала."""
from pathlib import Path

import pytest

from app.parsers.step_features import parse_features, read_features
from app.parsers.step_reader import read_step
from tests.fixtures.samples import make_box_step
from tests.fixtures.step_dfm import make_dfm_step

JOURNAL_STEP = Path(__file__).resolve().parents[2] / "data" / "uploads" / "journal"


def test_hole_diameter_and_depth():
    f = parse_features(make_dfm_step(holes=[(20, 20, 4, 15), (60, 30, 10, 20)]))
    assert sorted((h.diameter, h.depth) for h in f.holes) == [(4, 15), (10, 20)]
    assert f.holes[0].ratio == pytest.approx(3.75)  # самое глубокое — первым
    assert f.internal_radii == []


def test_hole_split_in_two_halves_is_one_hole():
    f = parse_features(make_dfm_step(holes=[(20, 20, 3, 18)], split=True))
    assert [(h.diameter, h.depth) for h in f.holes] == [(3, 18)]


def test_partial_concave_cylinder_is_internal_radius_and_boss_ignored():
    f = parse_features(make_dfm_step(fillets=[(10, 10, 0.5, 20)], bosses=[(50, 30, 12, 5)]))
    assert f.holes == [] and f.internal_radii == [0.5]


def test_coaxial_holes_far_apart_are_separate():
    f = parse_features(make_dfm_step(holes=[(20, 20, 3, 4)], bottom_holes=[(20, 20, 3, 4)], c=20))
    assert [(h.diameter, h.depth) for h in f.holes] == [(3, 4), (3, 4)]  # не одно отверстие глубиной 20


def test_undetermined_without_single_body():
    assert parse_features(make_dfm_step(holes=[(20, 20, 4, 15)], bodies=2)) is None
    assert parse_features("мусор") is None
    assert read_features("нет-такого-файла.step") is None
    f = parse_features(make_box_step())
    assert f is not None and f.holes == [] and f.internal_radii == []


@pytest.mark.skipif(not JOURNAL_STEP.exists(), reason="нет STEP журнала калибровки в data/uploads")
def test_real_journal_steps_parse_sanely():
    files = sorted(JOURNAL_STEP.glob("*/*.step"))
    if not files:
        pytest.skip("нет STEP журнала калибровки")
    for path in files:
        dims = read_step(path).dims
        f = read_features(path)
        if f is None or dims is None:
            continue
        longest = max(dims.as_list())
        for h in f.holes:  # отверстие не глубже и не шире детали
            assert 0 < h.depth <= longest + 0.01 and 0 < h.diameter <= longest + 0.01, path.name
        assert all(0 < r <= longest for r in f.internal_radii), path.name
