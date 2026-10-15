"""Работа технолога с позицией: данные, сложность, аналог, маршрут, станок, расчёт (ФТ-05…ФТ-14)."""
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.analog import AnalogMatch, find_analogs
from app.domain.enums import CountMode, PositionStatus, Stage  # noqa: F401
from app.domain.history import diff_fields, require_reason
from app.domain.machines import allowed_machines, suggest_machine
from app.domain.models import Machine, StageNorm
from app.domain.route import remove_stage, route_from_list, route_to_list, set_stage
from app.domain.status import transition
from app.services.actor import Actor, ServiceError
from app.services.calc_service import build_calculation, position_features
from app.services.history_service import record
from app.services.auto_service import MANUAL_ROUTE, auto_prepare, mark_manual
from app.services.order_service import _apply_reading, ensure_editable, get_position
from app.services.reference_service import (
    complexity_scale,
    domain_machines,
    journal_entries,
    journal_entry,
)
from app.storage.orm import PositionRow

EDITABLE_FIELDS = {"dims", "volume", "material", "tolerance_grade", "threads", "technology"}
TECHNOLOGIES = {"milling", "turning"}

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
    edited = sorted(set((pos.data or {}).get("edited") or []) | {f for f, _, _ in diff})
    pos.data = {**(pos.data or {}), **new, "edited": edited}
    auto_prepare(session, pos)
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
        mark_manual(pos, "complexity")
        auto_prepare(session, pos)
    session.commit()
    return pos


def set_cooperation(session: Session, actor: Actor, position_id: int, flag: bool) -> PositionRow:
    pos = _editable(session, actor, position_id)
    if pos.cooperation != flag:
        record(session, actor, "position", pos.id, "cooperation", pos.cooperation, flag)
        pos.cooperation = flag
        pos.data = {**(pos.data or {}), "cooperation_manual": True}  # решение технолога сильнее ТТ чертежа
        auto_prepare(session, pos)
    session.commit()
    return pos


def analog_candidates(session: Session, position_id: int) -> list[AnalogMatch]:
    pos = get_position(session, position_id)
    return find_analogs(position_features(pos), journal_entries(session))


def choose_analog(session: Session, actor: Actor, position_id: int, code: str) -> PositionRow:
    """Технолог меняет аналог, выбранный системой; маршрут формируется по нему заново (ФТ-07, ФТ-09)."""
    pos = _editable(session, actor, position_id)
    journal_entry(session, code)
    record(session, actor, "position", pos.id, "analog", pos.analog_code, code, "выбран технологом")
    pos.analog_code, pos.no_analog = code, False
    mark_manual(pos, "analog")
    pos.data = {**pos.data, "auto": {**pos.data["auto"], "route": True}}
    old_route = pos.route
    auto_prepare(session, pos)
    record(session, actor, "position", pos.id, "route", old_route, pos.route, f"сформирован по аналогу {code}")
    session.commit()
    return pos


def choose_no_analog(session: Session, actor: Actor, position_id: int) -> PositionRow:
    pos = _editable(session, actor, position_id)
    record(session, actor, "position", pos.id, "analog", pos.analog_code, None, "аналога нет: ручной ввод")
    pos.analog_code, pos.no_analog = None, True
    pos.route = route_to_list(MANUAL_ROUTE)
    mark_manual(pos, "analog", "route")
    auto_prepare(session, pos)
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
    mark_manual(pos, "route")
    auto_prepare(session, pos)
    session.commit()
    return pos


def remove_route_stage(session: Session, actor: Actor, position_id: int, stage: Stage, reason: str) -> PositionRow:
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    old = pos.route
    pos.route = route_to_list(remove_stage(route_from_list(pos.route), Stage(stage)))
    record(session, actor, "position", pos.id, "route", old, pos.route, reason)
    mark_manual(pos, "route")
    auto_prepare(session, pos)
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
        mark_manual(pos, "machine")
        auto_prepare(session, pos)
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


def auto_recalculate(session: Session, actor: Actor, position_id: int) -> list[str]:
    """Перечитать файлы и пересчитать; поля, исправленные технологом, и его решения сохраняются."""
    pos = _editable(session, actor, position_id)
    current = dict(pos.data or {})
    edited = set(current.get("edited") or [])
    _apply_reading(pos, [e.material for e in journal_entries(session)])
    service = {k: v for k, v in current.items() if k not in pos.data}  # auto, edited, замечания
    pos.data = {**pos.data, **service, **{k: current[k] for k in edited if k in current}}
    issues = auto_prepare(session, pos)
    session.commit()
    return issues


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
