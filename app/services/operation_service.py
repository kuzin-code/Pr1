"""Учёт выполнения операций специалистами: очередь, «Начал» / «Закончил», статусы, факт, уведомления.

Операции создаются при утверждении позиции из зафиксированного расчёта. Окончание группы операций
(станочная: наладка + обработка; слесарно-финишная; контроль) записывается как факт группы
с точной длительностью — по нему пересчитываются k, план и срок (ФТ-20…ФТ-22).
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import FactKind, Operation, PositionStatus, Role, StageGroup
from app.domain.operations import (
    MACHINE_OPERATIONS,
    OPERATION_ROLES,
    OperationError,
    OpStatus,
    can_press,
    group_of,
    operations_for_role,
    plan_operations,
    refresh_statuses,
)
from app.domain.timing import StageTime
from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR
from app.services.actor import Actor
from app.services.fact_service import record_fact
from app.services.history_service import record
from app.services.plan_service import fact_ends, notify
from app.services.reference_service import machine_operations
from app.storage.orm import OperationRow, PositionRow

IN_WORK = (PositionStatus.APPROVED, PositionStatus.MACHINING, PositionStatus.BENCH, PositionStatus.INSPECTION)
_GROUP_FACT = {StageGroup.MACHINE: FactKind.MACHINE, StageGroup.BENCH: FactKind.BENCH}


def position_operations(session: Session, position_id: int) -> list[OperationRow]:
    return list(session.scalars(select(OperationRow).where(OperationRow.position_id == position_id)
                                .order_by(OperationRow.seq)))


def _refresh(rows: list[OperationRow]) -> None:
    for row, status in zip(rows, refresh_statuses([OpStatus(r.status) for r in rows])):
        row.status = status.value


def create_operations(session: Session, pos: PositionRow) -> list[OperationRow]:
    """Маршрут утверждённой позиции раскладывается на операции по оборудованию."""
    times = [StageTime.from_dict(t) for t in pos.approved_calc["stage_times"]]
    technology = (pos.data or {}).get("technology") or "milling"
    planned = plan_operations(times, technology, pos.machine_code, machine_operations(session))
    rows = [OperationRow(position_id=pos.id, seq=p.seq, stage=p.stage.value, operation=p.operation.value,
                         resource=p.resource, planned_hours=p.planned_hours, status=OpStatus.WAITING.value)
            for p in planned]
    session.add_all(rows)
    _refresh(rows)
    return rows


def ensure_all_operations(session: Session) -> int:
    """Операции для позиций, утверждённых до появления учёта операций; уже отмеченные группы — выполнены."""
    created = 0
    for pos in session.scalars(select(PositionRow).where(PositionRow.approved_seq.is_not(None))):
        if pos.status == PositionStatus.DONE.value or position_operations(session, pos.id) or not pos.approved_calc:
            continue
        rows = create_operations(session, pos)
        ends = fact_ends(session, pos.id)
        done_groups = {FactKind.MACHINE: {StageGroup.MACHINE, StageGroup.OTHER}, FactKind.BENCH: {StageGroup.BENCH}}
        finished = set().union(*(groups for kind, groups in done_groups.items() if kind in ends))
        for row in rows:
            if Operation(row.operation) is not Operation.INSPECTION and group_of(Operation(row.operation)) in finished:
                row.status = OpStatus.DONE.value
        _refresh(rows)
        created += 1
    session.commit()
    return created


def queue(session: Session, actor: Actor, machine: str | None = None) -> list[OperationRow]:
    """Очередь специалиста: его операции по утверждённым позициям (мастер видит все)."""
    require(actor.role, Action.DO_OPERATIONS)
    allowed = {op.value for op in operations_for_role(actor.role)}
    query = (select(OperationRow).join(PositionRow, PositionRow.id == OperationRow.position_id)
             .where(PositionRow.status.in_([s.value for s in IN_WORK]))
             .where(OperationRow.status != OpStatus.DONE.value)
             .where(OperationRow.operation.in_(allowed))
             .order_by(PositionRow.approved_seq, OperationRow.seq))
    if machine:
        query = query.where(OperationRow.resource == machine)
    return list(session.scalars(query))


def _get(session: Session, actor: Actor, op_id: int) -> tuple[OperationRow, list[OperationRow], PositionRow]:
    row = session.get(OperationRow, op_id)
    if row is None:
        raise OperationError("Операция не найдена")
    require(actor.role, Action.DO_OPERATIONS)
    if not can_press(actor.role, Operation(row.operation)):
        from app.domain.access import AccessDenied
        raise AccessDenied(f"Операцию «{Operation(row.operation).label}» отмечает другой специалист")
    pos = session.get(PositionRow, row.position_id)
    return row, position_operations(session, pos.id), pos


def _calendar(operation: Operation):
    return MACHINE_CALENDAR if operation in MACHINE_OPERATIONS else BENCH_CALENDAR


def _label(pos: PositionRow, row: OperationRow) -> str:
    where = f" на {row.resource}" if row.resource else ""
    return f"«{pos.designation}» ({pos.quantity} шт., заказ {pos.order.number}){where}"


def start(session: Session, actor: Actor, op_id: int, at: datetime | None = None) -> OperationRow:
    row, rows, pos = _get(session, actor, op_id)
    at = at or datetime.now().replace(second=0, microsecond=0)
    status = OpStatus(row.status)
    if status is OpStatus.IN_PROGRESS or status is OpStatus.DONE:
        raise OperationError("Операция уже начата" if status is OpStatus.IN_PROGRESS else "Операция уже выполнена")
    if status is OpStatus.WAITING:
        raise OperationError("Сначала должна быть выполнена предыдущая операция маршрута")
    previous = [r for r in rows if r.seq < row.seq and r.finished_at]
    if previous and at < previous[-1].finished_at:
        raise OperationError(f"Начало раньше окончания предыдущей операции ({previous[-1].finished_at:%d.%m %H:%M})")
    if row.resource and Operation(row.operation) in MACHINE_OPERATIONS:
        busy = session.scalar(select(OperationRow).where(OperationRow.resource == row.resource)
                              .where(OperationRow.status == OpStatus.IN_PROGRESS.value))
        if busy is not None:
            other = session.get(PositionRow, busy.position_id)
            raise OperationError(f"Станок {row.resource} занят: «{other.designation}» — сначала закончите её")
    row.status, row.started_at, row.started_by = OpStatus.IN_PROGRESS.value, at, actor.username
    if pos.status == PositionStatus.APPROVED.value:
        pos.status = PositionStatus.MACHINING.value  # изготовление (включая резку)
    record(session, actor, "position", pos.id, f"start_{row.operation}", None, at.isoformat())
    session.commit()
    return row


def _close_groups(session: Session, actor: Actor, pos: PositionRow, rows: list[OperationRow], at: datetime) -> None:
    """Завершённая группа операций записывается как факт группы с точной длительностью."""
    ends = fact_ends(session, pos.id)

    def done(group):
        members = [r for r in rows if group_of(Operation(r.operation)) is group]
        return members, all(r.status == OpStatus.DONE.value for r in members)

    machine_rows, machine_done = done(StageGroup.MACHINE)
    if machine_rows and machine_done and FactKind.MACHINE not in ends:
        record_fact(session, actor, pos.id, FactKind.MACHINE, at,
                    duration=sum(r.duration_hours or 0 for r in machine_rows), authorized=True)
    bench_rows, bench_done = done(StageGroup.BENCH)
    if machine_done and bench_done and FactKind.MACHINE in fact_ends(session, pos.id) \
            and FactKind.BENCH not in fact_ends(session, pos.id):
        record_fact(session, actor, pos.id, FactKind.BENCH, at,
                    duration=sum(r.duration_hours or 0 for r in bench_rows), authorized=True)
    inspection = [r for r in rows if r.operation == Operation.INSPECTION.value]
    if inspection and inspection[0].status == OpStatus.DONE.value and FactKind.BENCH in fact_ends(session, pos.id):
        record_fact(session, actor, pos.id, FactKind.INSPECTION, at,
                    duration=inspection[0].duration_hours or 0, authorized=True)


def _specialist(operation: Operation) -> Role:
    return next(r for r in OPERATION_ROLES[operation] if r is not Role.MASTER)


def finish(session: Session, actor: Actor, op_id: int, at: datetime | None = None) -> OperationRow:
    row, rows, pos = _get(session, actor, op_id)
    at = at or datetime.now().replace(second=0, microsecond=0)
    if row.status != OpStatus.IN_PROGRESS.value:
        raise OperationError("Операция не начата: сначала нажмите «Начал»")
    if at <= row.started_at:
        raise OperationError(f"Окончание раньше начала ({row.started_at:%d.%m %H:%M})")
    operation = Operation(row.operation)
    row.duration_hours = round(_calendar(operation).hours_between(row.started_at, at), 3)
    row.status, row.finished_at, row.finished_by = OpStatus.DONE.value, at, actor.username
    _refresh(rows)
    record(session, actor, "position", pos.id, f"finish_{row.operation}", None,
           {"at": at.isoformat(), "hours": row.duration_hours})
    session.flush()
    _close_groups(session, actor, pos, rows, at)

    over = row.duration_hours - row.planned_hours
    note = f"; превышение плана на {over:.1f} ч" if over > 0.2 * row.planned_hours and over > 0.1 else ""
    notify(session, Role.MASTER, f"op:{row.id}:done", pos.id, row.operation, at,
           f"{operation.label} выполнена: {_label(pos, row)} — {row.duration_hours:.2f} ч "
           f"(план {row.planned_hours:.2f} ч){note}")
    following = next((r for r in rows if r.seq > row.seq and r.status == OpStatus.READY.value), None)
    if following is not None:
        nxt = Operation(following.operation)
        notify(session, _specialist(nxt), f"op:{following.id}:ready", pos.id, following.operation, at,
               f"{nxt.label}: можно начинать — {_label(pos, following)}")
    session.commit()
    return row
