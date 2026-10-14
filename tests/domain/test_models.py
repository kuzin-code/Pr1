import pytest

from app.domain.enums import CountMode, Stage, StageGroup
from app.domain.models import Dimensions, Measurement, StageNorm


def test_dimensions_sorted_and_fit_ignore_orientation():
    part = Dimensions(20, 100, 60)
    assert part.sorted() == (100, 60, 20)
    assert part.fits_in(Dimensions(30, 70, 110))
    assert not part.fits_in(Dimensions(90, 70, 30))


def test_dimensions_max_deviation():
    assert Dimensions(100, 60, 20).max_deviation(Dimensions(55, 108, 21)) == 8


def test_dimensions_must_be_positive():
    with pytest.raises(ValueError):
        Dimensions(0, 1, 1)


def test_setup_always_per_batch():
    assert StageNorm(Stage.SETUP, 1.0, CountMode.PER_PIECE).count_mode is CountMode.PER_BATCH


def test_batch_hours_by_count_mode():
    assert StageNorm(Stage.MACHINING, 0.5, CountMode.PER_PIECE).batch_hours(10) == 5
    assert StageNorm(Stage.INSPECTION, 0.5, CountMode.PER_BATCH).batch_hours(10) == 0.5
    assert StageNorm(Stage.PROGRAMMING, 2, CountMode.ONCE).batch_hours(10) == 2


def test_stage_groups():
    assert Stage.SETUP.group is StageGroup.MACHINE
    assert Stage.MACHINING.group is StageGroup.MACHINE
    assert Stage.LOCKSMITH.group is StageGroup.BENCH
    assert Stage.FINISHING.group is StageGroup.BENCH
    assert Stage.INSPECTION.group is StageGroup.OTHER


def test_roundtrip_serialization():
    norm = StageNorm(Stage.FINISHING, 0.3, CountMode.PER_PIECE)
    assert StageNorm.from_dict(norm.to_dict()) == norm
    m = Measurement(StageGroup.BENCH, 2, 3, position_id=5)
    assert Measurement.from_dict(m.to_dict()) == m
    assert m.ratio == 1.5
