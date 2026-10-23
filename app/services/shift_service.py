"""Сменное задание на станок из текущего плана (shift_task.py)."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import AccessDenied, Action, can, require
from app.domain.enums import Operation
from app.domain.operations import MACHINE_OPERATIONS, operations_for_role
from app.domain.shift_task import Shift, SlotOp, shift_task
from app.services.actor import Actor
from app.services.plan_service import current_plan
from app.services.reference_service import get_machine
from app.storage.orm import MachineRow, OperationRow, PositionRow


def require_shift(actor: Actor, machine: MachineRow) -> None:
    """Задание видят все, кому доступен план; специалист у станка — задание станка своей роли."""
    if can(actor.role, Action.VIEW_PLAN):
        return
    require(actor.role, Action.DO_OPERATIONS)
    mine = {op.value for op in operations_for_role(actor.role)}
    if not mine & set(machine.operations or []):
        raise AccessDenied("Сменное задание доступно только по станку с вашими операциями")


def shift_for(session: Session, actor: Actor, code: str, day: date) -> tuple[MachineRow, Shift, dict]:
    """Станок, задание на смену и позиции задания."""
    machine = get_machine(session, code)
    require_shift(actor, machine)
    plan = current_plan(session)
    machine_ops = [op.value for op in MACHINE_OPERATIONS]
    rows = session.scalars(select(OperationRow).where(OperationRow.resource == code)
                           .where(OperationRow.operation.in_(machine_ops)).order_by(OperationRow.seq))
    ops: dict[int, list[SlotOp]] = {}
    for r in rows:
        ops.setdefault(r.position_id, []).append(SlotOp(r.id, r.operation, r.planned_hours or 0.0, r.status))
    shift = shift_task(plan.slots, ops, code, day)
    positions = {line.position_id: session.get(PositionRow, line.position_id) for line in shift.lines}
    return machine, shift, positions


def operation_title(value: str) -> str:
    return "наладка и обработка" if value == "machine" else Operation(value).label
