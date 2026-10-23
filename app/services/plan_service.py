"""План по станкам, срок, загрузка, отклонения и уведомления (ФТ-15…ФТ-17, ФТ-22, ФТ-23)."""
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import FactKind, PositionStatus, Role, Stage, StageGroup
from app.domain.load import LoadCell, daily_load, weekly_load
from app.domain.machine_work import OpMark, machine_work
from app.domain.notifications import due_events
from app.domain.scheduling import Plan, PlanInput, build_plan
from app.domain.status import order_done
from app.domain.timing import (StageTime, extra_machine_hours, group_planned_hours, primary_machine_hours,
                               stage_planned_hours)
from app.domain.work_calendar import BENCH_CALENDAR
from app.services.actor import Actor
from app.storage.orm import FactRow, MachineRow, NotificationRow, OperationRow, OrderRow, PositionRow


MACHINE_STAGES = {st.value for st in Stage if st.group is StageGroup.MACHINE}


def clock() -> datetime:
    """Текущий момент для плана; в тестах подменяется фиксированным временем."""
    return datetime.now()


def plan_calc(pos: PositionRow) -> dict | None:
    """Утверждённая позиция планируется по зафиксированному расчёту, остальные — по текущему."""
    return pos.approved_calc if pos.approved_seq is not None else pos.current_calc


def fact_ends(session: Session, position_id: int) -> dict[FactKind, datetime]:
    rows = session.scalars(select(FactRow).where(FactRow.position_id == position_id).order_by(FactRow.id))
    return {FactKind(r.kind): r.ended_at for r in rows}


def _started_ids(session: Session) -> set[int]:
    """Позиции, по которым работа начата: отметка «Начал» или введённый факт."""
    marked = session.scalars(select(OperationRow.position_id).where(OperationRow.started_at.is_not(None)))
    return set(marked) | set(session.scalars(select(FactRow.position_id)))


def _machine_starts(session: Session) -> dict[int, dict[str, datetime]]:
    """Фактическое начало станочной работы позиции по станкам: самая ранняя отметка «Начал» наладки/обработки."""
    result: dict[int, dict[str, datetime]] = {}
    rows = session.scalars(select(OperationRow).where(OperationRow.started_at.is_not(None)))
    for row in rows:
        if row.stage not in MACHINE_STAGES or not row.resource:
            continue
        starts = result.setdefault(row.position_id, {})
        if row.resource not in starts or row.started_at < starts[row.resource]:
            starts[row.resource] = row.started_at
    return result


def _inputs(session: Session) -> list[PlanInput]:
    items = []
    started = _started_ids(session)
    machine_starts = _machine_starts(session)
    for pos in session.scalars(select(PositionRow)):
        calc = plan_calc(pos)
        if not calc or not pos.machine_code:
            continue
        times = [StageTime.from_dict(t) for t in calc["stage_times"]]
        ends = fact_ends(session, pos.id)
        items.append(PlanInput(
            position_id=pos.id, order_id=pos.order_id, order_start=pos.order.start_date,
            machine_code=pos.machine_code,
            cutting_hours=stage_planned_hours(times, Stage.CUTTING),
            machine_hours=primary_machine_hours(times),
            extra_machines=tuple(extra_machine_hours(times)),
            bench_hours=group_planned_hours(times, StageGroup.BENCH),
            inspection_hours=stage_planned_hours(times, Stage.INSPECTION),
            approved_seq=pos.approved_seq,
            fact_machine_end=ends.get(FactKind.MACHINE),
            fact_bench_end=ends.get(FactKind.BENCH),
            fact_inspection_end=ends.get(FactKind.INSPECTION),
            started=pos.id in started,
            machine_started=tuple(sorted(machine_starts.get(pos.id, {}).items())),
        ))
    return items


def current_plan(session: Session, now: datetime | None = None) -> Plan:
    """План пересчитывается при каждом обращении: после факта и после изменения плана.
    Неначатая работа — не раньше текущего момента (now; по умолчанию — сейчас)."""
    busy = {m.code: m.busy_until for m in session.scalars(select(MachineRow)) if m.busy_until}
    return build_plan(_inputs(session), busy, now or clock())


def order_deadline(session: Session, order_id: int, plan: Plan | None = None) -> datetime | None:
    plan = plan or current_plan(session)
    finishes = [p.finish for p in plan.positions.values() if p.order_id == order_id]
    return max(finishes) if finishes else None


def order_is_done(session: Session, order_id: int) -> bool:
    order = session.get(OrderRow, order_id)
    return order_done([PositionStatus(p.status) for p in order.positions])


@dataclass
class LoadTables:
    daily: dict[str, dict[date, LoadCell]]
    weekly: dict[str, dict[tuple[int, int], LoadCell]]


def _op_marks(session: Session) -> list[OpMark]:
    """Станочные операции утверждённых позиций с отметками «Начал»/«Закончил» — для объёма работы."""
    rows = session.scalars(select(OperationRow).where(OperationRow.stage.in_(MACHINE_STAGES))
                           .order_by(OperationRow.position_id, OperationRow.seq))
    return [OpMark(r.resource, r.position_id, r.planned_hours or 0.0, r.started_at, r.finished_at)
            for r in rows if r.resource]


def load_tables(session: Session, plan: Plan | None = None, now: datetime | None = None) -> LoadTables:
    """Загрузка — объём работы: план плановыми часами, начатое — по отметкам операций (без простоя)."""
    now = now or clock()
    plan = plan or current_plan(session, now)
    works = machine_work(plan.slots, _op_marks(session), now)
    return LoadTables(daily_load(works), weekly_load(works))


@dataclass(frozen=True)
class Deviation:
    position_id: int
    baseline: datetime
    current: datetime

    @property
    def shift_hours(self) -> float:
        """Сдвиг срока в рабочих часах (8-часовой день); минус — раньше плана."""
        if self.current >= self.baseline:
            return BENCH_CALENDAR.hours_between(self.baseline, self.current)
        return -BENCH_CALENDAR.hours_between(self.current, self.baseline)


def deviations(session: Session, plan: Plan | None = None) -> list[Deviation]:
    """Последствия отклонения факта от плана для мастера и начальника участка."""
    plan = plan or current_plan(session)
    result = []
    for pos in session.scalars(select(PositionRow).where(PositionRow.baseline_finish.is_not(None))):
        planned = plan.positions.get(pos.id)
        if planned and planned.finish != pos.baseline_finish:
            result.append(Deviation(pos.id, pos.baseline_finish, planned.finish))
    return result


def sync_notifications(session: Session, now: datetime | None = None) -> list[NotificationRow]:
    now = now or datetime.now()
    existing = set(session.scalars(select(NotificationRow.key)))
    created = []
    for event in due_events(current_plan(session), now):
        if event.key in existing:
            continue
        pos = session.get(PositionRow, event.position_id)
        text = (f"{event.kind.label}: позиция «{pos.designation}», заказ {pos.order.number} "
                f"(плановое окончание {event.planned_at:%d.%m.%Y %H:%M})")
        row = NotificationRow(key=event.key, position_id=event.position_id, kind=event.kind.value,
                              planned_at=event.planned_at, text=text)
        session.add(row)
        created.append(row)
    session.commit()
    return created


def notify(session: Session, role: Role, key: str, position_id: int, kind: str, at: datetime, text: str) -> None:
    """Адресное уведомление роли (мастеру, следующему специалисту); повторно не создаётся."""
    if session.scalar(select(NotificationRow.id).where(NotificationRow.key == key)) is None:
        session.add(NotificationRow(key=key, role=role.value, position_id=position_id, kind=kind,
                                    planned_at=at, text=text))


def list_notifications(session: Session, actor: Actor, unread_only: bool = False) -> list[NotificationRow]:
    require(actor.role, Action.RECEIVE_NOTIFICATIONS)
    query = select(NotificationRow).order_by(NotificationRow.planned_at.desc(), NotificationRow.id.desc())
    if actor.role is Role.MASTER:
        query = query.where((NotificationRow.role == Role.MASTER.value) | NotificationRow.role.is_(None))
    else:
        query = query.where(NotificationRow.role == actor.role.value)
    if unread_only:
        query = query.where(NotificationRow.read.is_(False))
    return list(session.scalars(query))


def unread_count(session: Session, role: Role) -> int:
    """Число непрочитанных уведомлений роли (для счётчика в меню)."""
    query = select(func.count(NotificationRow.id)).where(NotificationRow.read.is_(False))
    if role is Role.MASTER:
        query = query.where((NotificationRow.role == Role.MASTER.value) | NotificationRow.role.is_(None))
    else:
        query = query.where(NotificationRow.role == role.value)
    return session.scalar(query) or 0


def mark_notifications_read(session: Session, actor: Actor) -> None:
    for row in list_notifications(session, actor, unread_only=True):
        row.read = True
    session.commit()
