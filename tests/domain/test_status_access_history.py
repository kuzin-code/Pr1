"""ФТ-18 (статусы), ФТ-19 (утверждение/возврат), п. 4.5 (права), ФТ-05 (история)."""
import pytest

from app.domain.access import Action, AccessDenied, can, require
from app.domain.enums import PositionStatus as S, Role
from app.domain.history import ReasonRequired, diff_fields, require_reason
from app.domain.status import StatusError, is_editable, order_done, transition


def test_full_status_path():
    path = list(S)
    current = path[0]
    for target in path[1:]:
        current = transition(current, target)
    assert current is S.DONE


@pytest.mark.parametrize("current,target", [
    (S.CALCULATION, S.APPROVED), (S.TECH_REVIEW, S.APPROVED), (S.DONE, S.CALCULATION),
    (S.APPROVED, S.TECH_REVIEW), (S.MACHINING, S.APPROVED),
])
def test_invalid_transitions_rejected(current, target):
    with pytest.raises(StatusError):
        transition(current, target)


def test_return_by_head_goes_to_tech_review():
    assert transition(S.AWAITING_APPROVAL, S.TECH_REVIEW) is S.TECH_REVIEW


def test_only_unapproved_positions_editable():
    assert is_editable(S.CALCULATION) and is_editable(S.TECH_REVIEW)
    assert not any(is_editable(s) for s in list(S)[2:])


def test_order_done_when_all_positions_done():
    assert order_done([S.DONE, S.DONE])
    assert not order_done([S.DONE, S.INSPECTION])
    assert not order_done([])


# Матрица прав п. 4.5: (действие, роли «вносит/видит»).
MATRIX = {
    Action.UPLOAD_ORDER: {Role.MASTER},
    Action.EDIT_TECH_DATA: {Role.TECHNOLOGIST},
    Action.ASSIGN_MACHINE: {Role.TECHNOLOGIST},
    Action.VIEW_MACHINE_ASSIGNMENT: {Role.TECHNOLOGIST, Role.MASTER, Role.HEAD},
    Action.SET_MACHINE_BUSY: {Role.MASTER},
    Action.APPROVE: {Role.HEAD},
    Action.FACT_MACHINE: {Role.MASTER, Role.OPERATOR},
    Action.FACT_BENCH: {Role.MASTER},
    Action.VIEW_PLAN: {Role.TECHNOLOGIST, Role.MASTER, Role.HEAD, Role.MANAGEMENT},
    Action.VIEW_COST: {Role.HEAD, Role.IT, Role.TECHNOLOGIST, Role.MASTER},
    Action.EDIT_RATES: {Role.IT},
    Action.EDIT_JOURNAL: {Role.TECHNOLOGIST},
    Action.IMPORT_JOURNAL: {Role.TECHNOLOGIST, Role.IT},
    Action.VIEW_REFERENCE: {Role.TECHNOLOGIST, Role.HEAD, Role.IT},
    Action.EDIT_REFERENCE: {Role.IT, Role.TECHNOLOGIST},
    Action.RECEIVE_NOTIFICATIONS: {Role.MASTER, Role.OPERATOR, Role.CUTTER, Role.LOCKSMITH, Role.INSPECTOR},
    Action.DO_OPERATIONS: {Role.MASTER, Role.OPERATOR, Role.CUTTER, Role.LOCKSMITH, Role.INSPECTOR},
    Action.VIEW_HISTORY: {Role.TECHNOLOGIST, Role.MASTER, Role.HEAD},
}


@pytest.mark.parametrize("action", list(MATRIX))
@pytest.mark.parametrize("role", list(Role))
def test_permission_matrix_every_cell(action, role):
    assert can(role, action) == (role in MATRIX[action])


def test_require_raises():
    with pytest.raises(AccessDenied):
        require(Role.OPERATOR, Action.VIEW_COST)
    require(Role.IT, Action.VIEW_COST)


def test_reason_required_for_corrections():
    with pytest.raises(ReasonRequired):
        require_reason("   ")
    assert require_reason(" уточнено по чертежу ") == "уточнено по чертежу"


def test_diff_fields():
    assert diff_fields({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == [("b", 2, 3), ("c", None, 4)]
