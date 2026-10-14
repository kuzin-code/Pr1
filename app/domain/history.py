"""История корректировок: кто, когда, что, причина (ФТ-05, ФТ-24)."""
from typing import Any


class ReasonRequired(ValueError):
    pass


def require_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not text:
        raise ReasonRequired("Для корректировки необходимо указать причину")
    return text


def diff_fields(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Изменённые поля: (поле, было, стало)."""
    return [(k, old.get(k), v) for k, v in new.items() if old.get(k) != v]
