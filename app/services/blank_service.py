"""Заготовка позиции (domain/blank.py): предложение системы, правка технологом, припуски и ряды в справочнике.

Предложение системы считается по текущим данным позиции и параметру «blank_rules» и не хранится.
Заготовка, заданная технологом, хранится в data["blank"] и пересчётом (ФТ-26) не перезаписывается.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.blank import (
    ROUND_GROUPS,
    TECHNOLOGIST,
    Blank,
    BlankError,
    BlankRules,
    manual_blank,
    suggest_blank,
)
from app.domain.enums import PositionStatus
from app.domain.history import require_reason
from app.domain.models import Dimensions
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.order_service import get_position
from app.storage.orm import Parameter, PositionRow

PARAM = "blank_rules"
ALLOWANCE_FIELDS = ("sheet_side_allowance_mm", "sheet_thickness_side_allowance_mm", "bar_diameter_side_allowance_mm",
                    "bar_length_side_allowance_mm", "size_step_mm")
# Позиции, по которым заготовитель будет резать: незаданная заготовка — задача технолога.
TODO_STATUSES = (PositionStatus.TECH_REVIEW.value, PositionStatus.AWAITING_APPROVAL.value,
                 PositionStatus.APPROVED.value)


def rules(session: Session) -> BlankRules | None:
    row = session.get(Parameter, PARAM)
    return BlankRules.from_dict(row.value if row else None)


def system_blank(session: Session, pos: PositionRow, current: BlankRules | None = None) -> Blank:
    data = pos.data or {}
    drawing = data.get("drawing") or {}
    try:
        dims = Dimensions.from_list(data.get("dims"))
    except (TypeError, ValueError):
        dims = None
    return suggest_blank(dims, data.get("material") or "", data.get("technology") or "milling", pos.quantity,
                         current if current is not None else rules(session), drawing.get("blank"),
                         drawing.get("sortament"))


def position_blank(session: Session, pos: PositionRow, current: BlankRules | None = None) -> Blank:
    """Заготовка, заданная технологом, иначе — предложение системы."""
    data = pos.data or {}
    stored = data.get("blank")
    if stored and stored.get("source") == TECHNOLOGIST:
        return Blank.from_dict(stored, data.get("material") or "")
    return system_blank(session, pos, current)


def blank_text(session: Session, pos: PositionRow) -> str | None:
    """Строка заготовки для уведомления; не определена — None."""
    blank = position_blank(session, pos)
    return blank.text if blank.defined else None


def missing_blanks(session: Session, positions: list[PositionRow] | None = None) -> list[tuple[PositionRow, Blank]]:
    """Позиции, где система заготовку не определила, а технолог ещё не задал (спорные случаи — технологу)."""
    if positions is None:
        positions = list(session.scalars(select(PositionRow).where(PositionRow.status.in_(TODO_STATUSES))
                                         .order_by(PositionRow.id)))
    current = rules(session)
    found = []
    for pos in positions:
        if pos.status in TODO_STATUSES:
            blank = position_blank(session, pos, current)
            if not blank.defined:
                found.append((pos, blank))
    return found


def _editable(session: Session, actor: Actor, position_id: int) -> PositionRow:
    """Заготовку технолог правит и после утверждения (на стоимость не влияет) — до выполнения позиции."""
    require(actor.role, Action.EDIT_TECH_DATA)
    pos = get_position(session, position_id)
    if pos.status == PositionStatus.DONE.value:
        raise ServiceError("Позиция выполнена: заготовка не меняется")
    return pos


def set_blank(session: Session, actor: Actor, position_id: int, kind: str, sizes: dict, quantity, note: str,
              reason: str) -> Blank:
    """Технолог задаёт заготовку; в историю — с причиной; пометка «задано технологом»."""
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    try:
        blank = manual_blank((pos.data or {}).get("material") or "", kind, sizes, quantity, note)
    except BlankError as err:
        raise ServiceError(str(err)) from None
    old = position_blank(session, pos)
    record(session, actor, "position", pos.id, "blank", _history(old), _history(blank), reason)
    pos.data = {**(pos.data or {}), "blank": blank.to_dict()}
    session.commit()
    return blank


def reset_blank(session: Session, actor: Actor, position_id: int, reason: str) -> Blank:
    """Вернуть предложение системы (заготовка технолога снимается, с причиной)."""
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    old = position_blank(session, pos)
    pos.data = {k: v for k, v in (pos.data or {}).items() if k != "blank"}
    blank = system_blank(session, pos)
    record(session, actor, "position", pos.id, "blank", _history(old), _history(blank), reason)
    session.commit()
    return blank


def _history(blank: Blank) -> str:
    text = f"{blank.text} ({blank.source_label})"
    return f"{text}; {blank.note}" if blank.note else text


def _number(name: str, raw) -> float:
    try:
        value = float(str(raw).replace(",", ".").strip())
    except ValueError:
        raise ServiceError(f"«{name}»: укажите число, мм") from None
    if not 0 <= value <= 1000:
        raise ServiceError(f"«{name}»: от 0 до 1000 мм")
    return value


def _series(name: str, raw, required: bool = True) -> list[float]:
    items = raw.replace(";", " ").split() if isinstance(raw, str) else list(raw or [])
    values = sorted({_number(name, v) for v in items})
    if (required and not values) or (values and values[0] <= 0):
        raise ServiceError(f"«{name}»: перечислите размеры больше нуля через пробел")
    return values


def set_rules(session: Session, actor: Actor, values: dict, reason: str) -> BlankRules:
    """Припуски и стандартные ряды заготовки меняет тот, кто ведёт справочник; в историю — с причиной."""
    require(actor.role, Action.EDIT_REFERENCE)
    reason = require_reason(reason)
    new = {k: _number(k, values[k]) for k in ALLOWANCE_FIELDS}
    new["sheet_thicknesses_mm"] = _series("ряд толщин листа", values["sheet_thicknesses_mm"])
    diameters = values.get("round_diameters_mm") or {}
    # Пустой ряд группы — круг из этого материала задаёт технолог.
    new["round_diameters_mm"] = {g: _series(f"ряд диаметров: {label}", diameters.get(g), required=False)
                                 for g, label in ROUND_GROUPS.items()}
    row = session.get(Parameter, PARAM)
    old = dict(row.value) if row else None
    if row is None:
        session.add(Parameter(key=PARAM, value=new))
    else:
        row.value = new
    record(session, actor, "parameters", PARAM, PARAM, old, new, reason)
    session.commit()
    return BlankRules.from_dict(new)
