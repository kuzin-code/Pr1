"""Ввод факта, длительность групп, пересчёт k (ФТ-12, ФТ-20, ФТ-21)."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.cost import cost_from_hours, deviation
from app.domain.enums import FactKind, PositionStatus as S, StageGroup
from app.domain.models import Rates
from app.domain.status import StatusError, transition
from app.domain.timing import StageTime, group_calc_hours
from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.order_service import get_position
from app.services.plan_service import current_plan, fact_ends
from app.services.reference_service import add_journal_measurement
from app.storage.orm import FactRow, PositionRow

_ACTION = {FactKind.MACHINE: Action.FACT_MACHINE, FactKind.BENCH: Action.FACT_BENCH,
           FactKind.INSPECTION: Action.FACT_BENCH}
_GROUP = {FactKind.MACHINE: StageGroup.MACHINE, FactKind.BENCH: StageGroup.BENCH}
_STATUS_AFTER = {FactKind.MACHINE: (S.MACHINING, S.BENCH), FactKind.BENCH: (S.BENCH, S.INSPECTION),
                 FactKind.INSPECTION: (S.INSPECTION, S.DONE)}


def start_machining(session: Session, actor: Actor, position_id: int) -> PositionRow:
    """Отметка начала изготовления на станке (включая резку)."""
    require(actor.role, Action.FACT_MACHINE)
    pos = get_position(session, position_id)
    pos.status = transition(S(pos.status), S.MACHINING).value
    session.commit()
    return pos


def _times(pos: PositionRow) -> list[StageTime]:
    return [StageTime.from_dict(t) for t in pos.approved_calc["stage_times"]]


def _duration(session: Session, pos: PositionRow, kind: FactKind, ended_at: datetime) -> float:
    if kind is FactKind.MACHINE:
        planned = current_plan(session).positions.get(pos.id)
        if planned is None:
            raise ServiceError("Позиция не стоит в плане станка")
        return MACHINE_CALENDAR.hours_between(planned.machine_possible_start, ended_at)
    ends = fact_ends(session, pos.id)
    start = ends[FactKind.MACHINE] if kind is FactKind.BENCH else ends[FactKind.BENCH]
    return BENCH_CALENDAR.hours_between(start, ended_at)


def record_fact(session: Session, actor: Actor, position_id: int, kind: FactKind,
                ended_at: datetime, now: datetime | None = None, duration: float | None = None,
                authorized: bool = False) -> FactRow:
    """Окончание группы этапов. duration — точная длительность по кнопкам «Начал»/«Закончил»."""
    if not authorized:
        require(actor.role, _ACTION[kind])
    pos = get_position(session, position_id)
    status = S(pos.status)
    expected, after = _STATUS_AFTER[kind]
    if kind is FactKind.MACHINE and status is S.APPROVED:
        status = transition(status, S.MACHINING)
    if status is not expected:
        raise StatusError(f"Отметка «{kind.label}» недоступна в статусе «{status.label}»: "
                          "ситуацию разбирает технолог")

    explicit = duration is not None
    duration = duration if explicit else _duration(session, pos, kind, ended_at)
    if duration <= 0 and not explicit:
        raise ServiceError(f"Отметка «{kind.label}» ({ended_at:%d.%m.%Y %H:%M}) раньше возможного начала работ "
                           "(даты начала заказа или окончания предыдущего этапа): проверьте дату и время")
    group = _GROUP.get(kind)
    calc_hours = group_calc_hours(_times(pos), group) if group else None
    fact = FactRow(position_id=pos.id, kind=kind.value, ended_at=ended_at, entered_by=actor.username,
                   entered_at=now or datetime.now(), duration_hours=duration, calc_hours=calc_hours)
    session.add(fact)
    # Замер пополняет журнал по аналогу; k для контроля не определяется (ФТ-12, ФТ-21).
    if group and pos.analog_code and calc_hours and duration > 0:
        add_journal_measurement(session, pos.analog_code, group, calc_hours, duration, pos.id, ended_at)
    pos.status = transition(status, after).value
    record(session, actor, "position", pos.id, f"fact_{kind.value}", None,
           {"ended_at": ended_at.isoformat(), "duration_hours": duration})
    session.commit()
    return fact


def actual_cost(session: Session, position_id: int) -> dict:
    """Фактическая себестоимость = фактическое время × те же ставки; отклонение от утверждённого расчёта."""
    pos = get_position(session, position_id)
    if not pos.approved_calc:
        raise ServiceError("Позиция не утверждена")
    rows = list(session.scalars(select(FactRow).where(FactRow.position_id == pos.id)))
    hours = {k: sum(r.duration_hours or 0 for r in rows if r.kind == k.value) for k in FactKind}
    rates = Rates(**pos.approved_calc["rates"])
    actual = cost_from_hours(hours[FactKind.MACHINE], hours[FactKind.BENCH], rates)
    calculated = pos.approved_calc["cost"]["batch_cost"]
    return {"machine_hours": hours[FactKind.MACHINE], "bench_hours": hours[FactKind.BENCH],
            "actual": actual, "calculated": calculated,
            "deviation": deviation(actual, calculated) if actual else None}
