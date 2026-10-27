"""Ручной приоритет срочной позиции: смена с причиной, сдвиг сроков, уведомления (queue_order.py)."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import Role
from app.domain.history import require_reason
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.order_service import get_position
from app.services.plan_service import current_plan, notify
from app.storage.orm import PositionRow

# Кому сообщить о смене очереди: менеджеру (сроки заказчикам), мастеру, начальнику участка, оператору станка.
NOTIFY_ROLES = (Role.MANAGER, Role.MASTER, Role.HEAD)  # решение пользователя 27.10.2026
URGENT, NORMAL = "urgent", "normal"


@dataclass(frozen=True)
class Shift:
    """Срок позиции до и после смены приоритета."""
    position_id: int
    designation: str
    order_number: str
    before: datetime
    after: datetime


@dataclass(frozen=True)
class PriorityChange:
    position_id: int
    old: int
    new: int
    shifts: list[Shift]

    def summary(self) -> str:
        if not self.shifts:
            return "Сроки позиций не изменились"
        moved = "; ".join(f"«{s.designation}» ({s.order_number}): {s.before:%d.%m %H:%M} → {s.after:%d.%m %H:%M}"
                          for s in self.shifts)
        return f"Сдвинулись сроки: {moved}"


def target_priority(pos: PositionRow, action: str) -> int:
    """«Срочно ↑» — на ступень выше текущего; «обычный» — 0."""
    if action == URGENT:
        return (pos.priority or 0) + 1
    if action == NORMAL:
        return 0
    raise ServiceError("Неизвестное действие с приоритетом")


def set_priority(session: Session, actor: Actor, position_id: int, priority: int, reason: str,
                 now: datetime | None = None) -> PriorityChange:
    require(actor.role, Action.SET_PRIORITY)
    reason = require_reason(reason)
    if priority < 0:
        raise ServiceError("Приоритет не может быть отрицательным")
    pos = get_position(session, position_id)
    old = pos.priority or 0
    before = current_plan(session)
    pos.priority = priority
    session.flush()
    after = current_plan(session)
    shifts = []
    for pid, planned in after.positions.items():
        was = before.positions.get(pid)
        if was is not None and was.finish != planned.finish:
            other = session.get(PositionRow, pid)
            shifts.append(Shift(pid, other.designation, other.order.number, was.finish, planned.finish))
    shifts.sort(key=lambda s: s.after)
    change = PriorityChange(pos.id, old, priority, shifts)
    record(session, actor, "position", pos.id, "priority", old, priority, reason)
    now = now or datetime.now()
    word = "повышен" if priority > old else "снижен" if priority < old else "подтверждён"
    text = (f"Изменён приоритет: «{pos.designation}», заказ {pos.order.number} — {word} ({old} → {priority}), "
            f"причина: {reason}. {change.summary()}")
    stamp = now.strftime("%Y%m%d%H%M%S%f")
    for role in NOTIFY_ROLES:
        notify(session, role, f"prio:{pos.id}:{stamp}:{role.value}", pos.id, "priority", now, text)
    session.commit()
    return change
