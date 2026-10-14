"""ФТ-09 (маршрут), ФТ-11 (плановое время), ФТ-13 (себестоимость), ФТ-10 (станки)."""
import pytest

from app.domain.cost import compute_cost, order_total
from app.domain.enums import CountMode, Stage
from app.domain.machines import allowed_machines, suggest_machine
from app.domain.models import Dimensions, PartFeatures, Rates, StageNorm
from app.domain.route import RouteError, remove_stage, route_from_entry, set_stage
from app.domain.timing import compute_stage_times, group_calc_hours, group_planned_hours
from app.domain.enums import StageGroup
from tests.domain.conftest import make_entry

RATES = Rates(machine=2000, bench=1000)


def test_route_from_journal_in_stage_order():
    route = route_from_entry(make_entry())
    assert [s.stage for s in route] == list(Stage)
    assert route == make_entry().stages


def test_set_and_remove_stage():
    route = route_from_entry(make_entry())
    route = set_stage(route, StageNorm(Stage.MACHINING, 0.8, CountMode.PER_PIECE))
    assert next(s for s in route if s.stage is Stage.MACHINING).hours == 0.8
    route = remove_stage(route, Stage.FINISHING)
    assert Stage.FINISHING not in [s.stage for s in route]
    route = set_stage(route, StageNorm(Stage.FINISHING, 0.2))
    assert [s.stage for s in route] == list(Stage)


def test_remove_missing_stage_errors():
    with pytest.raises(RouteError):
        remove_stage([], Stage.CUTTING)


def test_planned_time_is_calc_times_group_k():
    times = compute_stage_times(make_entry().stages, quantity=10, k_machine=1.2, k_bench=0.9)
    by_stage = {t.stage: t for t in times}
    assert by_stage[Stage.SETUP].calc_hours == 1.5  # один раз на партию
    assert by_stage[Stage.SETUP].planned_hours == pytest.approx(1.8)
    assert by_stage[Stage.MACHINING].calc_hours == 5.0
    assert by_stage[Stage.MACHINING].planned_hours == pytest.approx(6.0)
    assert by_stage[Stage.LOCKSMITH].planned_hours == pytest.approx(2.25)
    assert by_stage[Stage.CUTTING].k == 1.0
    assert by_stage[Stage.CUTTING].planned_hours == pytest.approx(2.0)
    assert by_stage[Stage.INSPECTION].planned_hours == 0.5
    assert group_calc_hours(times, StageGroup.MACHINE) == pytest.approx(6.5)
    assert group_planned_hours(times, StageGroup.MACHINE) == pytest.approx(7.8)


def test_cost_formula_reference_part():
    times = compute_stage_times(make_entry().stages, quantity=10, k_machine=1.0, k_bench=1.0)
    cost = compute_cost(times, quantity=10, rates=RATES)
    # машинное: 1.5 + 0.5*10 = 6.5 ч; слесарное: (0.25 + 0.1)*10 = 3.5 ч
    assert cost.machine_hours == pytest.approx(6.5)
    assert cost.bench_hours == pytest.approx(3.5)
    assert cost.batch_cost == pytest.approx(6.5 * 2000 + 3.5 * 1000)
    assert cost.piece_cost == pytest.approx(cost.batch_cost / 10)
    assert cost.rates == RATES


def test_cost_excludes_cutting_programming_inspection():
    route = [StageNorm(Stage.CUTTING, 5), StageNorm(Stage.PROGRAMMING, 5, CountMode.ONCE),
             StageNorm(Stage.INSPECTION, 5, CountMode.PER_BATCH)]
    cost = compute_cost(compute_stage_times(route, 1, 1, 1), 1, RATES)
    assert cost.batch_cost == 0


def test_order_total():
    times = compute_stage_times(make_entry().stages, 1, 1, 1)
    c = compute_cost(times, 1, RATES)
    assert order_total([c, c]) == pytest.approx(2 * c.batch_cost)


def test_allowed_machines_by_zone_and_technology(machines):
    part = PartFeatures(Dimensions(500, 300, 100), "Д16Т", 7, 3)
    codes = [m.code for m in allowed_machines(part, machines)]
    assert codes == ["VF2", "ST20Y"]  # SMM мал, пила не ЧПУ


def test_turning_part_only_on_turning_machine(machines):
    part = PartFeatures(Dimensions(50, 50, 120), "Сталь 45", 7, 3, technology="turning")
    assert [m.code for m in allowed_machines(part, machines)] == ["ST20Y"]


def test_suggest_machine_from_analog(machines):
    allowed = machines[:3]
    assert suggest_machine(allowed, make_entry(machine="SMM")).code == "SMM"
    assert suggest_machine(allowed, make_entry(machine="SAW")) is None
    assert suggest_machine(allowed, None) is None
