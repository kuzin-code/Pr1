"""Приём заказа → проверка технологом → расчёт → утверждение (ФТ-01…ФТ-14, ФТ-18, ФТ-19, ФТ-24)."""
import pytest

from app.domain.access import AccessDenied
from app.domain.enums import Mark, PositionStatus, Stage
from app.domain.history import ReasonRequired
from app.domain.order_intake import FileFormatError, OrderTooLargeError
from app.services import approval_service as approval
from app.services import order_service as orders
from app.services import reference_service as ref
from app.services import tech_service as tech
from app.services.actor import ServiceError
from app.services.history_service import list_history
from app.services.order_service import UploadedFile
from tests.services.conftest import HEAD, IT, MASTER, OPERATOR, TECH, part_files


def prepared(session, make_order, quantity=10):
    """Позиция, проверенная технологом и рассчитанная по аналогу DEMO-02."""
    order = make_order()
    pos = order.positions[0]
    orders.set_quantity(session, MASTER, pos.id, quantity)
    tech.update_data(session, TECH, pos.id, {"material": "Д16Т"}, "уточнено по штампу чертежа")
    tech.set_complexity(session, TECH, pos.id, 2)
    tech.choose_analog(session, TECH, pos.id, "DEMO-02")
    tech.assign_machine(session, TECH, pos.id, "VF2")
    tech.calculate(session, TECH, pos.id)
    return pos


def test_order_created_with_read_data(session, make_order):
    order = make_order()
    (pos,) = order.positions
    assert pos.designation == "Кронштейн-1"
    assert pos.status == PositionStatus.TECH_REVIEW.value  # рассчитана автоматически
    assert sorted(pos.data["dims"], reverse=True) == pytest.approx([152, 58, 40])
    assert pos.data["material"] == "D16T"
    assert pos.data["tolerance_grade"] == 9
    assert pos.read_data["step"]["bodies"] == 1


def test_only_master_uploads(session, tmp_path):
    with pytest.raises(AccessDenied):
        orders.create_order(session, TECH, None, part_files(), tmp_path)


def test_51_positions_rejected(session, make_order):
    files = []
    for i in range(51):
        files += part_files(f"D-{i}")
    with pytest.raises(OrderTooLargeError):
        make_order(files)


def test_other_format_rejected(session, make_order):
    with pytest.raises(FileFormatError):
        make_order(part_files() + [UploadedFile("model.sldprt", b"x")])


def test_unpaired_files_linked_by_technologist(session, make_order):
    files = [part_files("Вал-3")[0], part_files("Вал-03")[1]]
    order = make_order(files)
    a, b = order.positions
    assert a.needs_link and b.needs_link
    with pytest.raises(ServiceError, match="пары"):
        tech.calculate(session, TECH, a.id)
    merged = orders.link_files(session, TECH, a.id, b.id)
    assert not merged.needs_link
    assert len(orders.get_order(session, order.id).positions) == 1


def test_quantity_set_by_master_only(session, make_order):
    pos = make_order().positions[0]
    with pytest.raises(AccessDenied):
        orders.set_quantity(session, TECH, pos.id, 5)
    orders.set_quantity(session, MASTER, pos.id, 5)
    assert orders.get_position(session, pos.id).quantity == 5


def test_correction_saved_in_history_with_reason(session, make_order):
    pos = make_order().positions[0]
    with pytest.raises(ReasonRequired):
        tech.update_data(session, TECH, pos.id, {"material": "Д16Т"}, "")
    tech.update_data(session, TECH, pos.id, {"material": "Д16Т"}, "по чертежу")
    (h,) = [h for h in list_history(session, entity="position", entity_id=pos.id) if h.field == "material"]
    assert (h.username, h.old, h.new, h.reason) == ("technolog", "D16T", "Д16Т", "по чертежу")


def test_complexity_estimated_automatically(session, make_order):
    pos = make_order().positions[0]  # H9, без резьб
    assert pos.complexity == 3
    assert pos.data["auto"]["complexity"] is True


def test_analog_candidates_and_machine_suggestion(session, make_order):
    pos = make_order().positions[0]
    tech.update_data(session, TECH, pos.id, {"material": "Д16Т"}, "по чертежу")
    tech.set_complexity(session, TECH, pos.id, 2)
    codes = [m.entry.part_code for m in tech.analog_candidates(session, pos.id)]
    assert codes == ["DEMO-02"]
    tech.choose_analog(session, TECH, pos.id, "DEMO-02")
    allowed, suggested = tech.machine_options(session, pos.id)
    assert suggested.code == "VF2"
    assert "ST20Y" not in [m.code for m in allowed]  # фрезерная деталь


def test_calculation_snapshot_cost_and_marks(session, make_order):
    pos = prepared(session, make_order)
    calc = orders.get_position(session, pos.id).current_calc
    assert pos.status == PositionStatus.TECH_REVIEW.value
    assert calc["analog_code"] == "DEMO-02"
    assert calc["rates"] == {"machine": 2000, "bench": 1000}
    # машинное: 1.0 + 0.6×10 = 7 ч; слесарное: (0.2 + 0.1)×10 = 3 ч
    assert calc["cost"]["batch_cost"] == pytest.approx(7 * 2000 + 3 * 1000)
    assert calc["cost"]["piece_cost"] == pytest.approx(1700)
    assert Mark.APPROXIMATE.value in calc["marks"]
    assert Mark.FEW_MEASUREMENTS.value in calc["marks"]
    assert calc["journal_version"] == ref.journal_version(session)


def test_route_edit_requires_reason_and_invalidates_calc(session, make_order):
    pos = prepared(session, make_order)
    with pytest.raises(ReasonRequired):
        tech.edit_route_stage(session, TECH, pos.id, Stage.MACHINING, 0.7, "piece", "")
    tech.edit_route_stage(session, TECH, pos.id, Stage.MACHINING, 0.7, "piece", "сложнее аналога")
    calc = orders.get_position(session, pos.id).current_calc  # пересчитано сразу
    assert calc["cost"]["machine_hours"] == pytest.approx(8)
    assert any(h.field == "route" and h.reason == "сложнее аналога"
               for h in list_history(session, entity="position", entity_id=pos.id))


def test_no_analog_manual_times(session, make_order):
    pos = make_order().positions[0]
    tech.set_complexity(session, TECH, pos.id, 5)
    assert tech.analog_candidates(session, pos.id) == []
    tech.choose_no_analog(session, TECH, pos.id)
    tech.assign_machine(session, TECH, pos.id, "VF2")
    with pytest.raises(ServiceError, match="время"):
        tech.calculate(session, TECH, pos.id)
    tech.edit_route_stage(session, TECH, pos.id, Stage.MACHINING, 2, "piece", "ручной ввод")
    calc = tech.calculate(session, TECH, pos.id)
    assert calc["analog_code"] is None
    assert calc["k"]["machine"]["value"] == 1.0
    assert Mark.NO_ANALOG.value in calc["marks"]


def test_cooperation_mark(session, make_order):
    pos = prepared(session, make_order)
    tech.set_cooperation(session, TECH, pos.id, True)
    calc = tech.calculate(session, TECH, pos.id)
    assert Mark.NO_COOPERATION.value in calc["marks"]


def test_submit_return_approve(session, make_order):
    pos = prepared(session, make_order)
    with pytest.raises(AccessDenied):
        approval.approve(session, OPERATOR, pos.id)
    with pytest.raises(ServiceError):
        approval.approve(session, HEAD, pos.id)  # ещё не передана на утверждение
    tech.submit(session, TECH, pos.id)
    assert pos.status == PositionStatus.AWAITING_APPROVAL.value
    approval.return_position(session, HEAD, pos.id, "проверить маршрут")
    assert pos.status == PositionStatus.TECH_REVIEW.value
    assert pos.current_calc is not None  # пересчитан при возврате
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    assert pos.status == PositionStatus.APPROVED.value
    assert pos.approved_seq == 1
    assert pos.approved_calc["cost"] == pos.current_calc["cost"]
    assert pos.baseline_finish is not None


def test_approved_position_cannot_be_edited(session, make_order):
    pos = prepared(session, make_order)
    tech.submit(session, TECH, pos.id)
    approval.approve(session, HEAD, pos.id)
    with pytest.raises(ServiceError, match="утвержд"):
        tech.update_data(session, TECH, pos.id, {"material": "АМг6"}, "ошибка")
    with pytest.raises(ServiceError):
        tech.calculate(session, TECH, pos.id)


def test_rate_change_does_not_change_saved_calculation(session, make_order):
    pos = prepared(session, make_order)
    before = dict(pos.current_calc)
    ref.set_rates(session, IT, 3000, 1500, "новые ставки")
    assert orders.get_position(session, pos.id).current_calc == before
    assert tech.calculate(session, TECH, pos.id)["rates"] == {"machine": 3000, "bench": 1500}


def test_submit_requires_machine(session, make_order):
    pos = make_order(part_files("Балка", dims=(2000, 30, 30))).positions[0]
    assert pos.machine_code is None
    assert any("Ни один станок" in i for i in pos.data["auto_issues"])
    with pytest.raises(ServiceError, match="станок"):
        tech.submit(session, TECH, pos.id)
