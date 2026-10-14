"""Статусы позиции и переходы (ФТ-18, ФТ-19)."""
from app.domain.enums import PositionStatus

ORDER = list(PositionStatus)
EDITABLE = {PositionStatus.CALCULATION, PositionStatus.TECH_REVIEW}


class StatusError(ValueError):
    pass


def next_status(current: PositionStatus) -> PositionStatus | None:
    idx = ORDER.index(current)
    return ORDER[idx + 1] if idx + 1 < len(ORDER) else None


def can_transition(current: PositionStatus, target: PositionStatus) -> bool:
    if target is next_status(current):
        return True
    # Возврат начальником участка на пересчёт.
    return current is PositionStatus.AWAITING_APPROVAL and target is PositionStatus.TECH_REVIEW


def transition(current: PositionStatus, target: PositionStatus) -> PositionStatus:
    if not can_transition(current, target):
        raise StatusError(f"Недопустимый переход: «{current.label}» → «{target.label}»")
    return target


def is_editable(status: PositionStatus) -> bool:
    """Утверждённая позиция вручную не меняется (ФТ-19)."""
    return status in EDITABLE


def order_done(statuses: list[PositionStatus]) -> bool:
    return bool(statuses) and all(s is PositionStatus.DONE for s in statuses)
