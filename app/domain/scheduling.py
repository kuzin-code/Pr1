"""Очередь станков и срок изготовления (ФТ-15, ФТ-16, ФТ-22).

Ограниченный ресурс — только станки с ЧПУ. Позиции на станке идут в порядке утверждения с учётом ручного
приоритета срочных (queue_order.py), неутверждённые (предварительные) — после утверждённых. Этапы позиции последовательны:
резка → станочная группа → слесарно-финишная → контроль. Программирование в срок не входит.
План и загрузка считаются только по плановому времени (решение пользователя 23.10.2026): отметки
«Начал»/«Закончил» и факты окончания групп — для прослеживаемости операций, на план не влияют.
Логика «по факту» (ниже) включается константой PLAN_FROM_MARKS. При ней введённый факт окончания группы
заменяет плановое окончание и сдвигает последующие работы.
Неначатая позиция (нет отметки начала и фактов) планируется не раньше текущего момента — план не уходит
в прошлое; начатая и выполненная работа остаётся как есть.
Работа на станке по факту: начало — отметка «Начал» первой станочной операции на этом станке; позиции, уже
начатые или выполненные на станке, занимают его раньше неначатых (это уже случилось). Полоса на станке
никогда не кончается раньше начала: если факт окончания раньше расчётного начала и отметки «Начал» нет,
начало отсчитывается от факта назад на плановую длительность.
"""
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from app.domain.queue_order import QueueItem, queue_order
from app.domain.work_calendar import BENCH_CALENDAR, MACHINE_CALENDAR

# False — план и загрузка только по плановому времени; True — по отметкам «Начал»/«Закончил» и фактам
# (начало — отметка станочной операции, окончание — факт, загрузка — фактическая работа без простоя).
PLAN_FROM_MARKS = False


def from_marks_enabled(value: bool | None = None) -> bool:
    """Явное значение или текущий переключатель PLAN_FROM_MARKS."""
    return PLAN_FROM_MARKS if value is None else value


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
    machine_started: tuple = ()  # ((код станка, фактическое начало первой станочной операции), …)
    priority: int = 0  # ручной приоритет срочной позиции: больше — раньше в очереди

    @property
    def on_machine(self) -> bool:
        """Работа на станке уже начата или выполнена."""
        return bool(self.machine_started) or self.fact_machine_end is not None


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
    hours: float | None = None  # плановый объём работы на станке (наладка и обработка), ч


@dataclass
class Plan:
    positions: dict[int, PositionPlan] = field(default_factory=dict)
    slots: list[MachineSlot] = field(default_factory=list)


def work_start(order_start: date, now: datetime | None) -> datetime:
    """Начало неначатой работы: начало смены в дату начала заказа, но не раньше «сейчас» (по календарю)."""
    start = BENCH_CALENDAR.day_start(order_start)
    return BENCH_CALENDAR.normalize(max(start, now)) if now else start


def _ordered(items: list[PlanInput], marks: bool) -> list[PlanInput]:
    """Очередь: по приоритету и порядку утверждения; начатые не вытесняются.
    По факту (marks) работа, уже начатая или выполненная на станке, занимает его раньше неначатой."""
    by_id = {it.position_id: it for it in items}

    def order(group):
        return [by_id[k] for k in queue_order([QueueItem(it.position_id, it.approved_seq, it.position_id,
                                                         it.priority, it.started) for it in group])]
    if marks:
        return order([it for it in items if it.on_machine]) + order([it for it in items if not it.on_machine])
    return order(items)


def _fit_to_fact(slots: list[MachineSlot], hours: list[float], fact_end: datetime) -> list[MachineSlot]:
    """Последняя полоса кончается фактом; полосы идут подряд и не бывают «конец раньше начала»."""
    result = list(slots)
    bound = fact_end
    for i in range(len(result) - 1, -1, -1):
        s = result[i]
        end = bound if i == len(result) - 1 else min(s.end, bound)
        start = s.start if s.start <= end else MACHINE_CALENDAR.sub_hours(end, hours[i])
        result[i] = MachineSlot(s.machine_code, s.position_id, start, end, s.preliminary, s.hours)
        bound = start
    return result


def _plan_only(item: PlanInput) -> PlanInput:
    """Вход без отметок и фактов: остаётся только признак «начата» (не переносится на «сейчас»)."""
    return replace(item, machine_started=(), fact_machine_end=None, fact_bench_end=None, fact_inspection_end=None)


def build_plan(items: list[PlanInput], machine_busy_until: dict[str, datetime],
               now: datetime | None = None, from_marks: bool | None = None) -> Plan:
    """now — текущий момент: неначатые позиции не планируются раньше него (None — как от даты начала).
    from_marks — учитывать отметки и факты (None — по переключателю PLAN_FROM_MARKS)."""
    marks = from_marks_enabled(from_marks)
    if not marks:
        items = [_plan_only(it) for it in items]
    plan = Plan()
    machine_free: dict[str, datetime] = dict(machine_busy_until)
    for it in _ordered(items, marks):
        if not it.machine_code:
            continue
        actual = dict(it.machine_started)
        preliminary = it.approved_seq is None
        start = work_start(it.order_start, None if it.started else now)
        cutting_end = BENCH_CALENDAR.add_hours(start, it.cutting_hours)
        free = machine_free.get(it.machine_code)
        possible_start = max(start, free) if free else start
        if it.machine_code in actual:
            machine_start = actual[it.machine_code]
            possible_start = min(possible_start, machine_start)
        else:
            machine_start = MACHINE_CALENDAR.normalize(max(cutting_end, possible_start))
        slot_end = MACHINE_CALENDAR.add_hours(machine_start, it.machine_hours)
        slots = [MachineSlot(it.machine_code, it.position_id, machine_start, slot_end, preliminary,
                             it.machine_hours)]
        hours = [it.machine_hours]
        for code, extra_hours in it.extra_machines:
            if code in actual:
                extra_start = actual[code]
            else:
                busy = machine_free.get(code)
                extra_start = MACHINE_CALENDAR.normalize(max(slot_end, busy) if busy else slot_end)
            slot_end = MACHINE_CALENDAR.add_hours(extra_start, extra_hours)
            slots.append(MachineSlot(code, it.position_id, extra_start, slot_end, preliminary, extra_hours))
            hours.append(extra_hours)
        if it.fact_machine_end:  # факт окончания станочной группы — по последнему станку
            slots = _fit_to_fact(slots, hours, it.fact_machine_end)
        for extra in slots[1:]:
            machine_free[extra.machine_code] = extra.end
        machine_start, machine_end = slots[0].start, slots[-1].end
        bench_end = it.fact_bench_end or BENCH_CALENDAR.add_hours(machine_end, it.bench_hours)
        finish = it.fact_inspection_end or BENCH_CALENDAR.add_hours(bench_end, it.inspection_hours)
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
