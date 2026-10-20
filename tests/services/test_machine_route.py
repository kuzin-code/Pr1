"""Смена оборудования и второй станок в техпроцессе: время и себестоимость пересчитываются (ФТ-10, ФТ-11, ФТ-13)."""
import pytest

from app.domain.enums import Operation, Stage
from app.services import approval_service as approval
from app.services import operation_service as ops
from app.services import plan_service as plans
from app.services import tech_service as tech
from app.services.actor import ServiceError
from tests.services.conftest import HEAD, TECH


def machine_hours(calc, machine=None):
    return sum(t["planned_hours"] for t in calc["stage_times"]
               if t["stage"] in ("setup", "machining") and t.get("machine") == machine)


def test_machine_change_takes_time_from_parts_made_on_that_machine(session, make_order):
    pos = make_order().positions[0]  # фрезерная, Д16Т, 152×58×40 — система ставит VF2
    assert pos.machine_code == "VF2"
    before = pos.current_calc["cost"]["batch_cost"]
    tech.assign_machine(session, TECH, pos.id, "SMM")
    assert pos.machine_code == "SMM"
    assert pos.analog_code in ("DEMO-04", "DEMO-09")  # детали журнала, сделанные на SMM
    assert pos.current_calc["machine_code"] == "SMM"
    assert pos.current_calc["cost"]["batch_cost"] != before
    assert any("SMM" in r for r in pos.data["analog_reasons"])


def test_second_machine_adds_setup_and_machining_time_and_cost(session, make_order):
    pos = make_order().positions[0]
    base_cost = pos.current_calc["cost"]["batch_cost"]
    tech.add_machine(session, TECH, pos.id, "ST20Y")
    extra = pos.data["extra_machines"]
    assert [e["machine"] for e in extra] == ["ST20Y"] and extra[0]["technology"] == "turning"
    assert extra[0]["setup"] > 0 and extra[0]["machining"] > 0
    calc = pos.current_calc
    added = machine_hours(calc, "ST20Y")
    assert added == pytest.approx(extra[0]["setup"] + extra[0]["machining"] * pos.quantity, rel=0.01)
    assert calc["cost"]["batch_cost"] == pytest.approx(base_cost + added * calc["rates"]["machine"], rel=0.01)
    stages = [(t["stage"], t.get("machine")) for t in calc["stage_times"]]
    assert stages.index(("machining", "ST20Y")) == stages.index(("machining", None)) + 2  # сразу после станка 1


def test_second_machine_in_plan_and_operations(session, make_order):
    pos = make_order().positions[0]
    tech.add_machine(session, TECH, pos.id, "ST20Y")
    plan = plans.current_plan(session)
    slots = [s for s in plan.slots if s.position_id == pos.id]
    assert [s.machine_code for s in slots] == ["VF2", "ST20Y"] and slots[1].start >= slots[0].end
    assert plan.positions[pos.id].machine_end == slots[1].end
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    created = [(o.operation, o.resource) for o in ops.position_operations(session, pos.id)]
    assert (Operation.MILLING.value, "VF2") in created and (Operation.TURNING.value, "ST20Y") in created
    assert created.index((Operation.TURNING.value, "ST20Y")) < created.index((Operation.LOCKSMITH.value, "AKB1000"))


def test_technologist_edits_second_machine_time_and_removes_it(session, make_order):
    pos = make_order().positions[0]
    base_cost = pos.current_calc["cost"]["batch_cost"]
    tech.add_machine(session, TECH, pos.id, "ST20Y")
    tech.set_extra_machine_time(session, TECH, pos.id, "ST20Y", setup=1.0, machining=0.5, reason="по опыту")
    assert machine_hours(pos.current_calc, "ST20Y") == pytest.approx(1.0 + 0.5 * pos.quantity)
    tech.remove_machine(session, TECH, pos.id, "ST20Y")
    assert not pos.data.get("extra_machines")
    assert pos.current_calc["cost"]["batch_cost"] == pytest.approx(base_cost)


def test_second_machine_must_differ_and_fit(session, make_order):
    pos = make_order().positions[0]
    with pytest.raises(ServiceError):
        tech.add_machine(session, TECH, pos.id, "VF2")  # уже основной
    with pytest.raises(ServiceError):
        tech.add_machine(session, TECH, pos.id, "BS275")  # не станок с ЧПУ
