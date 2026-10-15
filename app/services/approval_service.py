"""Утверждение и возврат позиций начальником участка (ФТ-19, О-13)."""
import copy
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import PositionStatus
from app.domain.history import require_reason
from app.domain.status import StatusError, transition
from app.services.actor import Actor, ServiceError
from app.services.calc_service import build_calculation
from app.services.history_service import record
from app.services.order_service import get_position
from app.services.operation_service import create_operations
from app.services.plan_service import current_plan
from app.storage.orm import PositionRow


def _awaiting(session: Session, actor: Actor, position_id: int) -> PositionRow:
    require(actor.role, Action.APPROVE)
    pos = get_position(session, position_id)
    if pos.status != PositionStatus.AWAITING_APPROVAL.value:
        raise ServiceError("Позиция не ожидает утверждения")
    return pos


def approve(session: Session, actor: Actor, position_id: int, now: datetime | None = None) -> PositionRow:
    pos = _awaiting(session, actor, position_id)
    if pos.current_calc is None:
        raise ServiceError("Нет расчёта позиции")
    pos.status = transition(PositionStatus.AWAITING_APPROVAL, PositionStatus.APPROVED).value
    pos.approved_seq = (session.scalar(select(func.max(PositionRow.approved_seq))) or 0) + 1
    pos.approved_at = now or datetime.now()
    # Расчёт, зафиксированный на момент утверждения, — база для сравнения с фактом (п. 7.3).
    pos.approved_calc = copy.deepcopy(pos.current_calc)
    session.flush()
    planned = current_plan(session).positions.get(pos.id)
    pos.baseline_finish = planned.finish if planned else None
    create_operations(session, pos)  # операции по оборудованию для учёта «Начал» / «Закончил»
    record(session, actor, "position", pos.id, "status", PositionStatus.AWAITING_APPROVAL.value,
           pos.status, "утверждена")
    session.commit()
    return pos


def return_position(session: Session, actor: Actor, position_id: int, reason: str) -> PositionRow:
    """Возврат на пересчёт: статус «проверка технолога», расчёт пересчитывается."""
    pos = _awaiting(session, actor, position_id)
    reason = require_reason(reason)
    try:
        pos.status = transition(PositionStatus.AWAITING_APPROVAL, PositionStatus.TECH_REVIEW).value
    except StatusError as err:
        raise ServiceError(str(err)) from None
    try:
        pos.current_calc = build_calculation(session, actor, pos)
    except ServiceError:
        pos.current_calc = None
    record(session, actor, "position", pos.id, "status", PositionStatus.AWAITING_APPROVAL.value,
           pos.status, reason)
    session.commit()
    return pos
