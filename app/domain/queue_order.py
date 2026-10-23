"""Порядок очереди станков с ручным приоритетом срочных позиций.

Правило:
- утверждённые позиции идут раньше неутверждённых (предварительных);
- среди утверждённых — по приоритету (больше — раньше), при равном — по прежнему порядку утверждения;
- начатую позицию не вытесняет никто: позиция, стоявшая в очереди после начатой, не может её обогнать,
  какой бы приоритет ей ни поставили (начатые идут между собой в прежнем порядке);
- неутверждённые — после утверждённых, в прежнем порядке (приоритет у них не действует до утверждения).
Без приоритетов порядок совпадает с прежним: по порядку утверждения.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueItem:
    key: object  # идентификатор позиции
    approved_seq: int | None
    position_id: int
    priority: int = 0
    started: bool = False


def _base_key(item: QueueItem):
    approved = item.approved_seq is not None
    return (0 if approved else 1, item.approved_seq or 0, item.position_id)


def queue_order(items: list[QueueItem]) -> list:
    """Ключи позиций в порядке очереди."""
    base = sorted(items, key=_base_key)
    approved = [it for it in base if it.approved_seq is not None]
    rest = [it for it in base if it.approved_seq is None]
    result, pending = [], list(approved)
    while pending:
        barrier = next((i for i, it in enumerate(pending) if it.started), len(pending) - 1)
        # Кандидаты — до первой начатой включительно: позиции после неё её не обгоняют.
        candidates = pending[:barrier + 1]
        best = max(range(len(candidates)), key=lambda i: (candidates[i].priority, -i))
        result.append(pending.pop(best).key)
    return result + [it.key for it in rest]
