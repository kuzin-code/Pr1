"""Уведомления мастеру в момент планового окончания этапов (ФТ-23)."""
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import FactKind
from app.domain.scheduling import Plan


@dataclass(frozen=True)
class DueEvent:
    position_id: int
    kind: FactKind
    planned_at: datetime

    @property
    def key(self) -> str:
        return f"{self.position_id}:{self.kind.value}"


def due_events(plan: Plan, now: datetime) -> list[DueEvent]:
    """Наступившие плановые окончания по утверждённым позициям."""
    events = []
    for p in plan.positions.values():
        if p.preliminary:
            continue
        for kind, moment in ((FactKind.MACHINE, p.machine_end), (FactKind.BENCH, p.bench_end),
                             (FactKind.INSPECTION, p.finish)):
            if moment <= now:
                events.append(DueEvent(p.position_id, kind, moment))
    return sorted(events, key=lambda e: (e.planned_at, e.position_id))
