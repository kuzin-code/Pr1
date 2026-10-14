"""Себестоимость обработки (ФТ-13).

Себестоимость = машинное время × ставка машинного + слесарное время × ставка слесарного.
Машинное — наладка и обработка; слесарное — слесарная и финишная.
Резка, программирование, контроль, материалы, накладные и кооперация не входят.
"""
from dataclasses import dataclass

from app.domain.enums import StageGroup
from app.domain.models import Rates
from app.domain.timing import StageTime, group_planned_hours


@dataclass(frozen=True)
class CostResult:
    machine_hours: float
    bench_hours: float
    rates: Rates
    quantity: int

    @property
    def batch_cost(self) -> float:
        return self.machine_hours * self.rates.machine + self.bench_hours * self.rates.bench

    @property
    def piece_cost(self) -> float:
        return self.batch_cost / self.quantity

    def to_dict(self) -> dict:
        return {
            "machine_hours": self.machine_hours,
            "bench_hours": self.bench_hours,
            "rates": self.rates.to_dict(),
            "quantity": self.quantity,
            "batch_cost": self.batch_cost,
            "piece_cost": self.piece_cost,
        }


def compute_cost(times: list[StageTime], quantity: int, rates: Rates) -> CostResult:
    return CostResult(
        machine_hours=group_planned_hours(times, StageGroup.MACHINE),
        bench_hours=group_planned_hours(times, StageGroup.BENCH),
        rates=rates,
        quantity=quantity,
    )


def cost_from_hours(machine_hours: float, bench_hours: float, rates: Rates) -> float:
    """Фактическая себестоимость = фактическое время × те же ставки (ФТ-21)."""
    return machine_hours * rates.machine + bench_hours * rates.bench


def order_total(costs: list[CostResult]) -> float:
    return sum(c.batch_cost for c in costs)


def deviation(actual: float, calculated: float) -> float:
    """Отклонение |факт − расчёт| ÷ факт (п. 7.3)."""
    if actual == 0:
        raise ValueError("Фактическая себестоимость равна нулю")
    return abs(actual - calculated) / actual
