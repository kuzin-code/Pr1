"""Очередь станков и срок изготовления (ФТ-15, ФТ-16, ФТ-22).

Ограниченный ресурс — только станки с ЧПУ. Позиции на станке идут в порядке утверждения,
неутверждённые (предварительные) — после утверждённых. Этапы позиции последовательны:
резка → станочная группа → слесарно-финишная → контроль. Программирование в срок не входит.
Введённый факт окончания группы заменяет плановое окончание и сдвигает последующие работы.
Неначатая позиция (нет отметки начала и фактов) планируется не раньше текущего момента — план не уходит
в прошлое; начатая и выполненная работа остаётся как есть.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR


@dataclass(frozen=True)
class PlanInput:
    position_id: int
    order_id: int
    order_start: date
    machine_code: str | None
    cutting_hours: float
    machine_hours: float
    bench_hours: float
    inspection_hours: float
    approved_seq: int | None = None
    extra_machines: tuple = ()  # ((код станка, часы), …) — второй станок техпроцесса после основного
    fact_machine_end: datetime | None = None
    fact_bench_end: datetime | None = None
    fact_inspection_end: datetime | None = None
    started: bool = False  # есть отметка начала операции или факт — план позиции «сейчас» не сдвигает


@dataclass(frozen=True)
class PositionPlan:
    position_id: int
    order_id: int
    machine_code: str
    cutting_end: datetime
    machine_possible_start: datetime  # для длительности факта (ФТ-21)
    machine_start: datetime
    machine_end: datetime
    bench_end: datetime
    finish: datetime  # окончание внутреннего контроля
    preliminary: bool


@dataclass(frozen=True)
class MachineSlot:
    machine_code: str
    position_id: int
    start: datetime
    end: datetime
    preliminary: bool


@dataclass
class Plan:
    positions: dict[int, PositionPlan] = field(default_factory=dict)
    slots: list[MachineSlot] = field(default_factory=list)


def work_start(order_start: date, now: datetime | None) -> datetime:
    """Начало неначатой работы: начало смены в дату начала заказа, но не раньше «сейчас» (по календарю)."""
    start = BENCH_CALENDAR.day_start(order_start)
    return BENCH_CALENDAR.normalize(max(start, now)) if now else start


def _queue_key(item: PlanInput):
    approved = item.approved_seq is not None
    return (0 if approved else 1, item.approved_seq or 0, item.position_id)


def build_plan(items: list[PlanInput], machine_busy_until: dict[str, datetime],
               now: datetime | None = None) -> Plan:
    """now — текущий момент: неначатые позиции не планируются раньше него (None — как от даты начала)."""
    plan = Plan()
    machine_free: dict[str, datetime] = dict(machine_busy_until)
    for it in sorted(items, key=_queue_key):
        if not it.machine_code:
            continue
        start = work_start(it.order_start, None if it.started else now)
        cutting_end = BENCH_CALENDAR.add_hours(start, it.cutting_hours)
        free = machine_free.get(it.machine_code)
        possible_start = max(start, free) if free else start
        machine_start = MACHINE_CALENDAR.normalize(max(cutting_end, possible_start))
        machine_end = MACHINE_CALENDAR.add_hours(machine_start, it.machine_hours)
        slots = [MachineSlot(it.machine_code, it.position_id, machine_start, machine_end, it.approved_seq is None)]
        for code, hours in it.extra_machines:
            busy = machine_free.get(code)
            extra_start = MACHINE_CALENDAR.normalize(max(machine_end, busy) if busy else machine_end)
            extra_end = MACHINE_CALENDAR.add_hours(extra_start, hours)
            slots.append(MachineSlot(code, it.position_id, extra_start, extra_end, it.approved_seq is None))
            machine_free[code] = extra_end
            machine_end = extra_end
        if it.fact_machine_end:  # факт окончания станочной группы — по последнему станку
            last = slots[-1]
            slots[-1] = MachineSlot(last.machine_code, last.position_id, last.start, it.fact_machine_end,
                                    last.preliminary)
            machine_end = it.fact_machine_end
            if len(slots) > 1:
                machine_free[last.machine_code] = it.fact_machine_end
        bench_end = it.fact_bench_end or BENCH_CALENDAR.add_hours(machine_end, it.bench_hours)
        finish = it.fact_inspection_end or BENCH_CALENDAR.add_hours(bench_end, it.inspection_hours)
        preliminary = it.approved_seq is None
        plan.positions[it.position_id] = PositionPlan(
            it.position_id, it.order_id, it.machine_code, cutting_end, possible_start,
            machine_start, machine_end, bench_end, finish, preliminary,
        )
        plan.slots.extend(slots)
        first_end = slots[0].end
        machine_free[it.machine_code] = max(first_end, free) if free else first_end
    return plan


def order_deadline(plan: Plan, position_ids: list[int]) -> datetime | None:
    """Срок заказа — максимальный срок по его позициям."""
    finishes = [plan.positions[p].finish for p in position_ids if p in plan.positions]
    return max(finishes) if finishes else None
