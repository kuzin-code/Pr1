"""Операции оборудования и учёт их выполнения специалистами (кнопки «Начал» / «Закончил»)."""
import pytest

from app.domain.enums import CountMode, Operation, Role, Stage
from app.domain.operations import (
    OpStatus,
    OperationError,
    can_press,
    normalize_operations,
    operation_for_stage,
    plan_operations,
    refresh_statuses,
)
from app.domain.timing import compute_stage_times
from app.domain.models import StageNorm

MACHINE_OPS = {
    "VF2": ["setup", "milling"], "ST20Y": ["setup", "turning"], "BS275": ["cutting"],
    "AKB1000": ["locksmith", "finishing"],
}


def route():
    return [StageNorm(Stage.CUTTING, 0.1), StageNorm(Stage.PROGRAMMING, 2, CountMode.ONCE),
            StageNorm(Stage.SETUP, 1, CountMode.PER_BATCH), StageNorm(Stage.MACHINING, 0.5),
            StageNorm(Stage.LOCKSMITH, 0.2), StageNorm(Stage.FINISHING, 0.1),
            StageNorm(Stage.INSPECTION, 0.5, CountMode.PER_BATCH)]


def test_operation_for_stage_by_technology():
    assert operation_for_stage(Stage.MACHINING, "milling") is Operation.MILLING
    assert operation_for_stage(Stage.MACHINING, "turning") is Operation.TURNING
    assert operation_for_stage(Stage.PROGRAMMING, "milling") is None  # вне границ, пропускается
    assert operation_for_stage(Stage.SETUP, "turning") is Operation.SETUP


def test_legacy_machining_normalized_by_machine_technology():
    assert normalize_operations(["setup", "machining"], ["milling"]) == ["setup", "milling"]
    assert normalize_operations(["setup", "machining"], ["turning"]) == ["setup", "turning"]
    assert normalize_operations(None, ["milling"]) == []


def test_plan_operations_assigns_equipment_and_skips_programming():
    ops = plan_operations(compute_stage_times(route(), 10, 1, 1), "milling", "VF2", MACHINE_OPS)
    assert [(o.operation, o.resource) for o in ops] == [
        (Operation.CUTTING, "BS275"), (Operation.SETUP, "VF2"), (Operation.MILLING, "VF2"),
        (Operation.LOCKSMITH, "AKB1000"), (Operation.FINISHING, "AKB1000"), (Operation.INSPECTION, None)]
    assert ops[2].planned_hours == pytest.approx(5)
    assert [o.seq for o in ops] == [1, 2, 3, 4, 5, 6]


def test_turning_part_goes_to_lathe():
    ops = plan_operations(compute_stage_times(route(), 1, 1, 1), "turning", "ST20Y", MACHINE_OPS)
    assert (ops[2].operation, ops[2].resource) == (Operation.TURNING, "ST20Y")


def test_zero_time_stage_skipped():
    r = [StageNorm(Stage.SETUP, 1, CountMode.PER_BATCH), StageNorm(Stage.MACHINING, 0.5),
         StageNorm(Stage.FINISHING, 0)]
    ops = plan_operations(compute_stage_times(r, 1, 1, 1), "milling", "VF2", MACHINE_OPS)
    assert [o.operation for o in ops] == [Operation.SETUP, Operation.MILLING]


def test_statuses_follow_sequence():
    statuses = [OpStatus.WAITING] * 3
    assert refresh_statuses(statuses) == [OpStatus.READY, OpStatus.WAITING, OpStatus.WAITING]
    assert refresh_statuses([OpStatus.DONE, OpStatus.WAITING, OpStatus.WAITING]) == \
        [OpStatus.DONE, OpStatus.READY, OpStatus.WAITING]
    assert refresh_statuses([OpStatus.DONE, OpStatus.IN_PROGRESS, OpStatus.WAITING]) == \
        [OpStatus.DONE, OpStatus.IN_PROGRESS, OpStatus.WAITING]


@pytest.mark.parametrize("role,operation,allowed", [
    (Role.OPERATOR, Operation.SETUP, True), (Role.OPERATOR, Operation.MILLING, True),
    (Role.OPERATOR, Operation.TURNING, True), (Role.OPERATOR, Operation.CUTTING, False),
    (Role.CUTTER, Operation.CUTTING, True), (Role.CUTTER, Operation.MILLING, False),
    (Role.LOCKSMITH, Operation.LOCKSMITH, True), (Role.LOCKSMITH, Operation.FINISHING, True),
    (Role.LOCKSMITH, Operation.INSPECTION, False), (Role.INSPECTOR, Operation.INSPECTION, True),
    (Role.MASTER, Operation.CUTTING, True), (Role.MASTER, Operation.INSPECTION, True),
    (Role.TECHNOLOGIST, Operation.MILLING, False), (Role.HEAD, Operation.INSPECTION, False),
])
def test_who_presses_buttons(role, operation, allowed):
    assert can_press(role, operation) is allowed


def test_operation_error_is_value_error():
    assert issubclass(OperationError, ValueError)
