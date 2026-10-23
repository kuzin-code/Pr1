"""Учёт выполнения операций специалистами: очередь, «Начал» / «Закончил», статусы, факт, уведомления.

Операции создаются при утверждении позиции из зафиксированного расчёта. Окончание группы операций
(станочная: наладка + обработка; слесарно-финишная; контроль) записывается как факт группы
с точной длительностью — по нему пересчитываются k, план и срок (ФТ-20…ФТ-22).
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import AccessDenied, Action, can, require
from app.domain.downtime import check_reason, group_reason, reason_label
from app.domain.enums import FactKind, Operation, PositionStatus, Role, StageGroup
from app.domain.history import require_reason
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
from app.services.reference_service import machine_operations, remove_measurements
from app.storage.orm import FactRow, NotificationRow, OperationRow, PositionRow

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


def require_position_files(session: Session, actor: Actor, position_id: int) -> None:
    """Чертёж PDF, STEP и 3D-модель: кто видит заказы — любой позиции; специалист — только утверждённой
    позиции в работе, где есть его операции (карточка позиции специалисту по-прежнему недоступна)."""
    require(actor.role, Action.VIEW_POSITION_FILES)
    if can(actor.role, Action.VIEW_ORDERS):
        return
    allowed = {op.value for op in operations_for_role(actor.role)}
    found = session.scalar(select(OperationRow.id).join(PositionRow, PositionRow.id == OperationRow.position_id)
                           .where(OperationRow.position_id == position_id)
                           .where(PositionRow.status.in_([s.value for s in IN_WORK]))
                           .where(OperationRow.operation.in_(allowed)).limit(1))
    if found is None:
        raise AccessDenied("Чертёж и модель доступны только по позициям с вашими операциями")


def _get(session: Session, actor: Actor, op_id: int) -> tuple[OperationRow, list[OperationRow], PositionRow]:
    row = session.get(OperationRow, op_id)
    if row is None:
        raise OperationError("Операция не найдена")
    require(actor.role, Action.DO_OPERATIONS)
    if not can_press(actor.role, Operation(row.operation)):
        raise AccessDenied(f"Операцию «{Operation(row.operation).label}» отмечает другой специалист")
    pos = session.get(PositionRow, row.position_id)
    return row, position_operations(session, pos.id), pos


def _calendar(operation: Operation):
    return MACHINE_CALENDAR if operation in MACHINE_OPERATIONS else BENCH_CALENDAR


def elapsed_hours(row: OperationRow, moment: datetime) -> float:
    """Сколько рабочих часов идёт начатая операция к моменту moment."""
    if row.started_at is None or moment <= row.started_at:
        return 0.0
    return _calendar(Operation(row.operation)).hours_between(row.started_at, moment)


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

    def reason(members):  # причина замера группы: потеря (не в k) важнее
        found = group_reason(r.reason for r in members)
        return found.value if found else None

    machine_rows, machine_done = done(StageGroup.MACHINE)
    if machine_rows and machine_done and FactKind.MACHINE not in ends:
        record_fact(session, actor, pos.id, FactKind.MACHINE, at, authorized=True, reason=reason(machine_rows),
                    duration=sum(r.duration_hours or 0 for r in machine_rows))
    bench_rows, bench_done = done(StageGroup.BENCH)
    if machine_done and bench_done and FactKind.MACHINE in fact_ends(session, pos.id)             and FactKind.BENCH not in fact_ends(session, pos.id):
        record_fact(session, actor, pos.id, FactKind.BENCH, at, authorized=True, reason=reason(bench_rows),
                    duration=sum(r.duration_hours or 0 for r in bench_rows))
    inspection = [r for r in rows if r.operation == Operation.INSPECTION.value]
    if inspection and inspection[0].status == OpStatus.DONE.value and FactKind.BENCH in fact_ends(session, pos.id):
        record_fact(session, actor, pos.id, FactKind.INSPECTION, at, authorized=True, reason=inspection[0].reason,
                    duration=inspection[0].duration_hours or 0)


def _specialist(operation: Operation) -> Role:
    return next(r for r in OPERATION_ROLES[operation] if r is not Role.MASTER)


def finish(session: Session, actor: Actor, op_id: int, at: datetime | None = None,
           reason: str | None = None, note: str = "") -> OperationRow:
    """«Закончил»; факт больше плана × порог — с причиной отклонения (downtime.py)."""
    row, rows, pos = _get(session, actor, op_id)
    at = at or datetime.now().replace(second=0, microsecond=0)
    if row.status != OpStatus.IN_PROGRESS.value:
        raise OperationError("Операция не начата: сначала нажмите «Начал»")
    if at <= row.started_at:
        raise OperationError(f"Окончание раньше начала ({row.started_at:%d.%m %H:%M})")
    operation = Operation(row.operation)
    duration = round(_calendar(operation).hours_between(row.started_at, at), 3)
    cause, note = check_reason(row.planned_hours, duration, reason, note)
    row.duration_hours, row.reason, row.reason_note = duration, cause.value if cause else None, note or None
    row.status, row.finished_at, row.finished_by = OpStatus.DONE.value, at, actor.username
    _refresh(rows)
    record(session, actor, "position", pos.id, f"finish_{row.operation}", None,
           {"at": at.isoformat(), "hours": row.duration_hours, "reason": row.reason}, note)
    session.flush()
    _close_groups(session, actor, pos, rows, at)

    over = row.duration_hours - row.planned_hours
    note = f"; превышение плана на {over:.1f} ч" if over > 0.2 * row.planned_hours and over > 0.1 else ""
    if row.reason:
        note += f", причина: {reason_label(row.reason)}" + (f" ({row.reason_note})" if row.reason_note else "")
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


# --- отмена ошибочной отметки (мастер) ------------------------------------------------------------

_FACT_ORDER = [FactKind.MACHINE, FactKind.BENCH, FactKind.INSPECTION]
_FACT_OF_GROUP = {StageGroup.MACHINE: FactKind.MACHINE, StageGroup.BENCH: FactKind.BENCH}


def _last_marked(rows: list[OperationRow]) -> OperationRow | None:
    started = [r for r in rows if r.started_at is not None]
    in_progress = [r for r in started if r.status == OpStatus.IN_PROGRESS.value]
    if in_progress:
        return in_progress[-1]
    return max(started, key=lambda r: (r.finished_at or r.started_at, r.seq), default=None)


def last_mark(session: Session, position_id: int) -> str | None:
    """Описание последней отметки позиции — что будет отменено."""
    row = _last_marked(position_operations(session, position_id))
    if row is None:
        return None
    label = Operation(row.operation).label
    if row.status == OpStatus.IN_PROGRESS.value:
        return f"«Начал»: {label}, {row.started_at:%d.%m %H:%M}"
    return f"«Закончил»: {label}, {row.finished_at:%d.%m %H:%M}"


def _drop_facts_from(session: Session, pos: PositionRow, first: FactKind) -> None:
    """Удаляет факт группы и последующие, вместе с их замерами в журнале калибровки."""
    for kind in _FACT_ORDER[_FACT_ORDER.index(first):]:
        for fact in session.scalars(select(FactRow).where(FactRow.position_id == pos.id)
                                    .where(FactRow.kind == kind.value)):
            group = {FactKind.MACHINE: StageGroup.MACHINE, FactKind.BENCH: StageGroup.BENCH}.get(kind)
            if group and pos.analog_code:
                remove_measurements(session, pos.analog_code, pos.id, group)
            session.delete(fact)
    session.flush()


def undo_last(session: Session, actor: Actor, position_id: int, reason: str) -> OperationRow:
    """Мастер отменяет последнюю отметку позиции: «Закончил» → в работе, «Начал» → можно начинать."""
    require(actor.role, Action.UNDO_OPERATIONS)
    reason = require_reason(reason)
    pos = session.get(PositionRow, position_id)
    rows = position_operations(session, position_id)
    row = _last_marked(rows)
    if pos is None or row is None:
        raise OperationError("По позиции нет отметок для отмены")
    description = last_mark(session, position_id)
    if row.status == OpStatus.DONE.value:
        fact = _FACT_OF_GROUP.get(group_of(Operation(row.operation)))
        if Operation(row.operation) is Operation.INSPECTION:
            fact = FactKind.INSPECTION
        if fact is not None:
            _drop_facts_from(session, pos, fact)
        for key in (f"op:{row.id}:done", *(f"op:{r.id}:ready" for r in rows if r.seq > row.seq)):
            session.query(NotificationRow).filter(NotificationRow.key == key).delete()
        row.status, row.finished_at, row.finished_by, row.duration_hours = OpStatus.IN_PROGRESS.value, None, None, None
        row.reason, row.reason_note = None, None
    else:
        row.status, row.started_at, row.started_by = OpStatus.WAITING.value, None, None
    _refresh(rows)
    ends = fact_ends(session, pos.id)
    if not any(r.started_at for r in rows):
        pos.status = PositionStatus.APPROVED.value
    elif FactKind.BENCH in ends and FactKind.INSPECTION not in ends:
        pos.status = PositionStatus.INSPECTION.value
    elif FactKind.MACHINE in ends and FactKind.BENCH not in ends:
        pos.status = PositionStatus.BENCH.value
    elif FactKind.MACHINE not in ends:
        pos.status = PositionStatus.MACHINING.value
    record(session, actor, "position", pos.id, "undo", description, None, reason)
    session.commit()
    return row
