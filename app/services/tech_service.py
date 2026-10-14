"""Работа технолога с позицией: данные, сложность, аналог, маршрут, станок, расчёт (ФТ-05…ФТ-14)."""
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.analog import AnalogMatch, find_analogs
from app.domain.enums import CountMode, PositionStatus, Stage
from app.domain.history import diff_fields, require_reason
from app.domain.machines import allowed_machines, suggest_machine
from app.domain.models import Machine, StageNorm
from app.domain.route import remove_stage, route_from_entry, route_from_list, route_to_list, set_stage
from app.domain.status import transition
from app.services.actor import Actor, ServiceError
from app.services.calc_service import build_calculation, position_features
from app.services.history_service import record
from app.services.order_service import ensure_editable, get_position
from app.services.reference_service import (
    complexity_scale,
    domain_machines,
    journal_entries,
    journal_entry,
)
from app.storage.orm import PositionRow

EDITABLE_FIELDS = {"dims", "volume", "material", "tolerance_grade", "threads", "technology"}
TECHNOLOGIES = {"milling", "turning"}

# Маршрут для ручного ввода, если аналога нет (ФТ-08).
MANUAL_ROUTE = [
    StageNorm(Stage.CUTTING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.PROGRAMMING, 0, CountMode.ONCE),
    StageNorm(Stage.SETUP, 0, CountMode.PER_BATCH),
    StageNorm(Stage.MACHINING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.LOCKSMITH, 0, CountMode.PER_PIECE),
    StageNorm(Stage.FINISHING, 0, CountMode.PER_PIECE),
    StageNorm(Stage.INSPECTION, 0, CountMode.PER_BATCH),
]


def _editable(session: Session, actor: Actor, position_id: int, action=Action.EDIT_TECH_DATA) -> PositionRow:
    require(actor.role, action)
    pos = get_position(session, position_id)
    ensure_editable(pos)
    return pos


def _normalize(field: str, value):
    if field == "dims":
        dims = [float(v) for v in value]
        if len(dims) != 3 or min(dims) <= 0:
            raise ServiceError("Габариты: три положительных размера, мм")
        return dims
    if field == "volume":
        return float(value) if value not in (None, "") else None
    if field == "tolerance_grade":
        grade = int(value)
        if not 1 <= grade <= 18:
            raise ServiceError("Квалитет должен быть от 1 до 18")
        return grade
    if field == "threads":
        items = value.split(",") if isinstance(value, str) else value
        return [t.strip() for t in items if t.strip()]
    if field == "technology":
        if value not in TECHNOLOGIES:
            raise ServiceError("Технология: milling или turning")
        return value
    return str(value).strip()


def update_data(session: Session, actor: Actor, position_id: int, changes: dict, reason: str) -> PositionRow:
    """Правка считанных данных; каждое изменение — в историю с причиной (ФТ-05)."""
    pos = _editable(session, actor, position_id)
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ServiceError(f"Недопустимые поля: {', '.join(sorted(unknown))}")
    new = {k: _normalize(k, v) for k, v in changes.items()}
    diff = diff_fields(pos.data or {}, new)
    if not diff:
        return pos
    reason = require_reason(reason)
    for field, old, value in diff:
        record(session, actor, "position", pos.id, field, old, value, reason)
    pos.data = {**(pos.data or {}), **new}
    pos.current_calc = None
    session.commit()
    return pos


def set_complexity(session: Session, actor: Actor, position_id: int, score: int, reason: str = "") -> PositionRow:
    pos = _editable(session, actor, position_id)
    scores = {int(s["score"]) for s in complexity_scale(session)}
    if score not in scores:
        raise ServiceError(f"Балл сложности должен быть из шкалы: {sorted(scores)}")
    if pos.complexity != score:
        record(session, actor, "position", pos.id, "complexity", pos.complexity, score, reason)
        pos.complexity = score
        pos.current_calc = None
    session.commit()
    return pos


def set_cooperation(session: Session, actor: Actor, position_id: int, flag: bool) -> PositionRow:
    pos = _editable(session, actor, position_id)
    if pos.cooperation != flag:
        record(session, actor, "position", pos.id, "cooperation", pos.cooperation, flag)
        pos.cooperation = flag
        pos.current_calc = None
    session.commit()
    return pos


def analog_candidates(session: Session, position_id: int) -> list[AnalogMatch]:
    pos = get_position(session, position_id)
    return find_analogs(position_features(pos), journal_entries(session))


def choose_analog(session: Session, actor: Actor, position_id: int, code: str) -> PositionRow:
    """Технолог подтверждает аналог; маршрут формируется по журналу (ФТ-07, ФТ-09)."""
    pos = _editable(session, actor, position_id)
    entry = journal_entry(session, code)
    record(session, actor, "position", pos.id, "analog", pos.analog_code, code, "подтверждён технологом")
    pos.analog_code, pos.no_analog = code, False
    route = route_to_list(route_from_entry(entry))
    record(session, actor, "position", pos.id, "route", pos.route, route, f"сформирован по аналогу {code}")
    pos.route = route
    pos.current_calc = None
    session.commit()
    return pos


def choose_no_analog(session: Session, actor: Actor, position_id: int) -> PositionRow:
    pos = _editable(session, actor, position_id)
    record(session, actor, "position", pos.id, "analog", pos.analog_code, None, "аналога нет: ручной ввод")
    pos.analog_code, pos.no_analog = None, True
    pos.route = route_to_list(MANUAL_ROUTE)
    pos.current_calc = None
    session.commit()
    return pos


def edit_route_stage(session: Session, actor: Actor, position_id: int, stage: Stage, hours: float,
                     count_mode: str, reason: str) -> PositionRow:
    """Правка этапа маршрута (время, способ счёта) с причиной (ФТ-09, ФТ-11)."""
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    try:
        norm = StageNorm(Stage(stage), float(hours), CountMode(count_mode))
    except ValueError as err:
        raise ServiceError(f"Некорректный этап: {err}") from None
    old = pos.route
    pos.route = route_to_list(set_stage(route_from_list(pos.route), norm))
    record(session, actor, "position", pos.id, "route", old, pos.route, reason)
    pos.current_calc = None
    session.commit()
    return pos


def remove_route_stage(session: Session, actor: Actor, position_id: int, stage: Stage, reason: str) -> PositionRow:
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    old = pos.route
    pos.route = route_to_list(remove_stage(route_from_list(pos.route), Stage(stage)))
    record(session, actor, "position", pos.id, "route", old, pos.route, reason)
    pos.current_calc = None
    session.commit()
    return pos


def machine_options(session: Session, position_id: int) -> tuple[list[Machine], Machine | None]:
    pos = get_position(session, position_id)
    allowed = allowed_machines(position_features(pos), domain_machines(session))
    analog = journal_entry(session, pos.analog_code) if pos.analog_code else None
    return allowed, suggest_machine(allowed, analog)


def assign_machine(session: Session, actor: Actor, position_id: int, code: str) -> PositionRow:
    """Станок назначает технолог из допустимых (ФТ-10)."""
    pos = _editable(session, actor, position_id, Action.ASSIGN_MACHINE)
    allowed, _ = machine_options(session, position_id)
    if code not in {m.code for m in allowed}:
        raise ServiceError(f"Станок «{code}» не подходит по рабочей зоне или технологии")
    if pos.machine_code != code:
        record(session, actor, "position", pos.id, "machine", pos.machine_code, code)
        pos.machine_code = code
        pos.current_calc = None
    session.commit()
    return pos


def calculate(session: Session, actor: Actor, position_id: int) -> dict:
    pos = _editable(session, actor, position_id)
    pos.current_calc = build_calculation(session, actor, pos)
    record(session, actor, "position", pos.id, "calculation", None, pos.current_calc, "расчёт")
    if pos.status == PositionStatus.CALCULATION.value:
        pos.status = transition(PositionStatus.CALCULATION, PositionStatus.TECH_REVIEW).value
    session.commit()
    return pos.current_calc


def submit(session: Session, actor: Actor, position_id: int) -> PositionRow:
    """Передача позиции на утверждение начальнику участка."""
    pos = _editable(session, actor, position_id)
    if not pos.machine_code:
        raise ServiceError("Назначьте станок перед передачей на утверждение")
    if pos.current_calc is None:
        raise ServiceError("Выполните расчёт перед передачей на утверждение")
    pos.status = transition(PositionStatus(pos.status), PositionStatus.AWAITING_APPROVAL).value
    record(session, actor, "position", pos.id, "status", PositionStatus.TECH_REVIEW.value, pos.status)
    session.commit()
    return pos
