"""Причины отклонения при «Закончил»: исправление мастером и сводка часов сверх плана (downtime.py)."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.downtime import DowntimeError, Overrun, OverrunTable, check_reason, group_reason, overrun_table, \
    reason_label
from app.domain.enums import FactKind, Operation, StageGroup
from app.domain.history import require_reason
from app.domain.operations import OpStatus, group_of
from app.services.actor import Actor
from app.services.history_service import record
from app.services.reference_service import set_measurement_reason
from app.storage.orm import FactRow, OperationRow, PositionRow

PERIODS = (("За 7 дней", 7), ("За 30 дней", 30))
_FACT_OF_GROUP = {StageGroup.MACHINE: FactKind.MACHINE, StageGroup.BENCH: FactKind.BENCH}


def set_reason(session: Session, actor: Actor, op_id: int, reason: str | None, note: str,
               comment: str) -> OperationRow:
    """Мастер исправляет причину отклонения выполненной операции — с комментарием (в историю).

    Причина переносится на факт группы и замер журнала калибровки: от неё зависит, идёт ли замер в k."""
    require(actor.role, Action.EDIT_DOWNTIME_REASON)
    comment = require_reason(comment)
    row = session.get(OperationRow, op_id)
    if row is None or row.status != OpStatus.DONE.value:
        raise DowntimeError("Причину можно исправить только у выполненной операции")
    cause, note = check_reason(row.planned_hours, row.duration_hours or 0, reason, note)
    old = reason_label(row.reason) if row.reason else None
    row.reason, row.reason_note = (cause.value if cause else None), note or None
    pos = session.get(PositionRow, row.position_id)
    operation = Operation(row.operation)
    kind = FactKind.INSPECTION if operation is Operation.INSPECTION else _FACT_OF_GROUP.get(group_of(operation))
    if kind is not None:  # резка в факт группы не входит
        members = [row] if kind is FactKind.INSPECTION else [
            r for r in session.scalars(select(OperationRow).where(OperationRow.position_id == pos.id))
            if group_of(Operation(r.operation)) is group_of(operation)]
        found = group_reason(r.reason for r in members)
        value = found.value if found else None
        for fact in session.scalars(select(FactRow).where(FactRow.position_id == pos.id)
                                    .where(FactRow.kind == kind.value)):
            fact.reason = value
        if kind is not FactKind.INSPECTION and pos.analog_code:
            set_measurement_reason(session, pos.analog_code, pos.id, group_of(operation), value)
    new = reason_label(row.reason) if row.reason else None
    record(session, actor, "position", pos.id, "downtime_reason", old, new, comment)
    session.commit()
    return row


def overruns(session: Session, since: datetime, until: datetime) -> OverrunTable:
    """Часы сверх плана выполненных операций, законченных в [since, until)."""
    rows = session.scalars(select(OperationRow).where(OperationRow.status == OpStatus.DONE.value)
                           .where(OperationRow.finished_at >= since).where(OperationRow.finished_at < until))
    return overrun_table([Overrun(r.resource or "", r.reason, r.planned_hours, r.duration_hours or 0)
                          for r in rows])


def overrun_report(session: Session, actor: Actor, now: datetime | None = None) -> list[tuple[str, OverrunTable]]:
    """Сводка для начальника участка и мастера: за последние 7 и 30 дней."""
    require(actor.role, Action.VIEW_DOWNTIME)
    now = now or datetime.now()
    return [(title, overruns(session, now - timedelta(days=days), now)) for title, days in PERIODS]
