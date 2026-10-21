"""Ступени количества в КП (1 / 10 / 50 шт.): цена и срок для нескольких количеств одной детали.

Расчёт тот же, что у позиции (ФТ-11, ФТ-13): время партии по маршруту и способу счёта этапов × k группы,
стоимость = часы × ставки. Наладка считается на партию, обработка — на штуку, поэтому цена за штуку
с ростом количества снижается. Готовность на станке — станочные часы по календарю станков от начала
работы станка над позицией (основной станок, затем второй станок техпроцесса подряд)."""
import re
from dataclasses import dataclass
from datetime import datetime

from app.domain.cost import compute_cost
from app.domain.models import Rates, StageNorm
from app.domain.order_intake import MAX_QUANTITY, QUANTITY_LIMIT_MESSAGE
from app.domain.timing import ExtraMachine, batch_times
from app.domain.work_calendar import MACHINE_CALENDAR

MAX_BREAKS = 4
MAX_BREAK_QUANTITY = MAX_QUANTITY  # ступень — как количество позиции: не больше 100 шт.


@dataclass(frozen=True)
class QuantityBreak:
    quantity: int
    machine_hours: float
    bench_hours: float
    batch_price: float
    ready: datetime | None = None  # окончание станочной обработки партии

    @property
    def piece_price(self) -> float:
        return self.batch_price / self.quantity

    def to_dict(self) -> dict:
        return {"quantity": self.quantity, "machine_hours": self.machine_hours, "bench_hours": self.bench_hours,
                "batch_price": self.batch_price, "piece_price": self.piece_price,
                "ready": self.ready.isoformat(timespec="minutes") if self.ready else None}


def parse_quantities(value) -> list[int]:
    """«1, 10, 50, 100» → [1, 10, 50, 100]: по возрастанию, без повторов, не больше MAX_BREAKS."""
    if value is None:
        return []
    parts = re.split(r"[\s,;]+", value.strip()) if isinstance(value, str) else [str(v) for v in value]
    try:
        quantities = sorted({int(p) for p in parts if p})
    except ValueError:
        raise ValueError("Ступени количества — целые числа через запятую, например 1, 10, 50") from None
    if any(q < 1 for q in quantities):
        raise ValueError("Ступени количества должны быть не меньше 1")
    if len(quantities) > MAX_BREAKS:
        raise ValueError(f"Не больше {MAX_BREAKS} ступеней количества")
    if any(q > MAX_BREAK_QUANTITY for q in quantities):
        raise ValueError(QUANTITY_LIMIT_MESSAGE)
    return quantities


def price_breaks(route: list[StageNorm], quantities: list[int], k_machine: float, k_bench: float, rates: Rates,
                 extra: list[ExtraMachine] = (), machine_start: datetime | None = None) -> list[QuantityBreak]:
    result = []
    for q in sorted(set(quantities)):
        cost = compute_cost(batch_times(route, q, k_machine, k_bench, extra), q, rates)
        ready = MACHINE_CALENDAR.add_hours(machine_start, cost.machine_hours) if machine_start else None
        result.append(QuantityBreak(q, cost.machine_hours, cost.bench_hours, cost.batch_cost, ready))
    return result
