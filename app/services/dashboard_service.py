"""Рабочий экран: сводка по участку на сегодня и задачи пользователя по его роли."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, can, require
from app.domain.enums import PositionStatus as S, Role
from app.domain.status import order_done
from app.services import plan_service
from app.services.actor import Actor
from app.services.file_check import check_entry_files
from app.services.history_service import list_history
from app.storage.orm import JournalRow, MachineRow, OrderRow, PositionRow

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
PRODUCTION = {S.MACHINING.value, S.BENCH.value, S.INSPECTION.value}
EVENT_LABELS = {
    "auto_calculation": "рассчитана автоматически", "calculation": "пересчитана", "status": "сменён статус",
    "quantity": "изменено количество", "material": "исправлен материал", "dims": "исправлены габариты",
    "complexity": "изменён балл сложности", "analog": "выбран аналог", "machine": "назначен станок",
    "route": "изменён маршрут", "files": "прикреплены файлы", "list": "принят заказ по списку",
    "add": "добавлена в журнал", "update": "изменена в журнале", "stages": "изменено время операций",
    "measurement": "добавлен замер", "rates": "изменены ставки", "operations": "изменены операции станка",
}


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    value: float
    unit: str = ""
    hint: str = ""
    tone: str = ""  # ok / warn / err — цвет акцента
    link: str = ""


@dataclass(frozen=True)
class Task:
    position_id: int | None
    title: str
    detail: str
    action: str
    link: str
    cost: float | None = None


@dataclass(frozen=True)
class MachineWeek:
    code: str
    name: str
    hours: float
    percent: float


@dataclass(frozen=True)
class Deadline:
    order_id: int
    order_number: str
    customer: str
    deadline: datetime
    preliminary: bool


@dataclass(frozen=True)
class Event:
    ts: datetime
    text: str
    link: str = ""


@dataclass
class Dashboard:
    date_label: str
    greeting: str = "Добрый день"
    kpis: list[Kpi] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    tasks_title: str = "Мои задачи"
    machines: list[MachineWeek] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def greeting(moment: datetime) -> str:
    if moment.hour < 12:
        return "Доброе утро"
    return "Добрый вечер" if moment.hour >= 18 else "Добрый день"


def date_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]} {day.year}"


def _cost(pos: PositionRow) -> float | None:
    calc = pos.approved_calc or pos.current_calc
    return calc["cost"]["batch_cost"] if calc else None


def _tasks(session: Session, actor: Actor, positions: list[PositionRow], deviations) -> tuple[str, list[Task]]:
    show_cost = can(actor.role, Action.VIEW_COST)

    def task(pos, detail, action):
        return Task(pos.id, f"{pos.designation} · {pos.quantity} шт.", f"Заказ {pos.order.number}. {detail}",
                    action, f"/positions/{pos.id}", _cost(pos) if show_cost else None)

    if actor.role is Role.TECHNOLOGIST:
        tasks = [task(p, f"Замечаний системы: {len((p.data or {}).get('auto_issues') or [])}", "Проверить расчёт")
                 for p in positions if p.status == S.TECH_REVIEW.value]
        missing = sum(1 for row in session.scalars(select(JournalRow))
                      if not all(s.ok for s in check_entry_files(row)))
        if missing:
            tasks.append(Task(None, "Журнал калибровки", f"Деталей без исправных STEP/PDF: {missing}",
                              "Прикрепить файлы", "/journal"))
        return "Расчёты на проверку", tasks
    if actor.role is Role.HEAD:
        return "Позиции к утверждению", [task(p, "Расчёт проверен технологом", "Утвердить или вернуть")
                                         for p in positions if p.status == S.AWAITING_APPROVAL.value]
    if actor.role is Role.MASTER:
        tasks = [task(p, "Нет пары STEP+PDF — расчёт не запущен", "Прикрепить файлы")
                 for p in positions if p.needs_link and p.status in (S.CALCULATION.value, S.TECH_REVIEW.value)]
        unread = len(plan_service.list_notifications(session, actor, unread_only=True))
        if unread:
            tasks.insert(0, Task(None, "Уведомления", f"Новых: {unread}", "Открыть", "/notifications"))
        return "Требует внимания мастера", tasks
    by_id = {p.id: p for p in positions}
    return "Отставания от плана", [
        Task(d.position_id, f"{by_id[d.position_id].designation}", f"Заказ {by_id[d.position_id].order.number}. "
             f"Сдвиг срока +{d.shift_hours:.0f} раб. ч", "Загрузка станков", "/plan")
        for d in deviations if d.shift_hours > 0 and d.position_id in by_id]


def build_dashboard(session: Session, actor: Actor, now: datetime | None = None) -> Dashboard:
    require(actor.role, Action.VIEW_PLAN)
    now = now or datetime.now()
    board = Dashboard(date_label(now.date()), greeting(now))
    orders = list(session.scalars(select(OrderRow)))
    positions = list(session.scalars(select(PositionRow).order_by(PositionRow.id)))
    plan = plan_service.current_plan(session)
    deviations = plan_service.deviations(session, plan)

    open_orders = [o for o in orders if not order_done([S(p.status) for p in o.positions])]
    review = sum(p.status == S.TECH_REVIEW.value for p in positions)
    approval = sum(p.status == S.AWAITING_APPROVAL.value for p in positions)
    production = sum(p.status in PRODUCTION or p.status == S.APPROVED.value for p in positions)
    late = [d for d in deviations if d.shift_hours > 0]

    monday = now.date() - timedelta(days=now.weekday())
    week = {monday + timedelta(days=i) for i in range(5)}
    daily = plan_service.load_tables(session, plan).daily
    cnc = [m for m in session.scalars(select(MachineRow).order_by(MachineRow.id)) if m.is_cnc]
    for m in cnc:
        hours = sum(cell.hours for day, cell in daily.get(m.code, {}).items() if day in week)
        board.machines.append(MachineWeek(m.code, m.model, hours, hours / (m.hours_per_day * 5) * 100))
    capacity = sum(m.hours_per_day * 5 for m in cnc) or 1
    load = round(sum(m.hours for m in board.machines) / capacity * 100)

    view = "/orders" if can(actor.role, Action.VIEW_ORDERS) else ""
    board.kpis = [
        Kpi("orders", "Заказов в работе", len(open_orders), link=view),
        Kpi("review", "На проверке технолога", review, tone="warn" if review else "", link=view),
        Kpi("approval", "Ждут утверждения", approval, tone="warn" if approval else "", link=view),
        Kpi("production", "В производстве", production, hint="позиций", link="/plan"),
        Kpi("load", "Загрузка ЧПУ на неделе", load, unit="%", tone="err" if load > 100 else "ok", link="/plan"),
        Kpi("late", "Отстают от плана", len(late), hint="позиций", tone="err" if late else "ok", link="/plan"),
    ]
    board.tasks_title, board.tasks = _tasks(session, actor, positions, deviations)

    for order in open_orders:
        deadline = plan_service.order_deadline(session, order.id, plan)
        if deadline:
            board.deadlines.append(Deadline(order.id, order.number, order.customer or "", deadline,
                                            any(p.approved_seq is None for p in order.positions)))
    board.deadlines = sorted(board.deadlines, key=lambda d: d.deadline)[:6]

    hidden = not can(actor.role, Action.VIEW_COST)
    by_id = {p.id: p for p in positions}
    for h in list_history(session, limit=40):
        if hidden and h.entity == "parameters":
            continue
        what = EVENT_LABELS.get(h.field) or EVENT_LABELS.get(h.field.split("_")[0], h.field.replace("_", " "))
        pos = by_id.get(int(h.entity_id)) if h.entity == "position" and h.entity_id.isdigit() else None
        subject = {"position": f"«{pos.designation}»" if pos else f"позиция {h.entity_id}",
                   "journal": f"журнал: {h.entity_id}",
                   "machine": f"станок {h.entity_id}", "order": f"заказ {h.entity_id}"}.get(h.entity, h.entity)
        link = f"/positions/{h.entity_id}" if h.entity == "position" and view else ""
        board.events.append(Event(h.ts, f"{subject} — {what} ({h.username})", link))
        if len(board.events) == 8:
            break
    return board
