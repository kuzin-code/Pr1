"""ФТ-12: k = факт ÷ расчёт, среднее по замерам, «ориентировочно» при < 5 замеров."""
import pytest

from app.domain.calibration import MIN_MEASUREMENTS, add_measurement, k_for
from app.domain.enums import StageGroup
from tests.domain.conftest import machine_measurements, make_entry


def test_no_measurements_gives_k_1_and_approximate():
    k = k_for(make_entry(), StageGroup.MACHINE)
    assert k.value == 1.0
    assert k.count == 0
    assert k.approximate


def test_k_is_mean_of_ratios():
    entry = make_entry(measurements=machine_measurements(1.2, 1.4))
    k = k_for(entry, StageGroup.MACHINE)
    assert k.value == pytest.approx(1.3)
    assert k.count == 2
    assert k.approximate


def test_k_per_group_independent():
    entry = make_entry(measurements=machine_measurements(1.5))
    assert k_for(entry, StageGroup.BENCH).value == 1.0


def test_mark_removed_at_five_measurements():
    entry = make_entry(measurements=machine_measurements(*[1.1] * (MIN_MEASUREMENTS - 1)))
    assert k_for(entry, StageGroup.MACHINE).approximate
    entry = add_measurement(entry, StageGroup.MACHINE, calc_hours=10, actual_hours=11)
    k = k_for(entry, StageGroup.MACHINE)
    assert not k.approximate
    assert k.value == pytest.approx(1.1)


def test_add_measurement_returns_new_entry():
    original = make_entry()
    updated = add_measurement(original, StageGroup.BENCH, 2, 3)
    assert original.measurements == []
    assert len(updated.measurements) == 1


def test_invalid_measurement_rejected():
    with pytest.raises(ValueError):
        add_measurement(make_entry(), StageGroup.MACHINE, 0, 3)
    with pytest.raises(ValueError):
        add_measurement(make_entry(), StageGroup.OTHER, 1, 3)


def test_zero_actual_time_is_not_a_measurement():
    with pytest.raises(ValueError):
        add_measurement(make_entry(), StageGroup.MACHINE, 7, 0)


def test_k_ignores_invalid_zero_measurements_in_existing_data():
    from app.domain.models import Measurement
    entry = make_entry(measurements=[Measurement(StageGroup.MACHINE, 7, 0), *machine_measurements(1.2)])
    k = k_for(entry, StageGroup.MACHINE)
    assert k.value == pytest.approx(1.2) and k.count == 1
