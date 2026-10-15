"""Загрузка станков, дата окончания текущих работ, уведомления (ФТ-15, ФТ-17, ФТ-22, ФТ-23)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import FactKind, Operation
from app.domain.gantt import build_gantt
from app.domain.operations import MACHINE_OPERATIONS
from app.services import operation_service, plan_service, reference_service
from app.storage.orm import PositionRow
from app.web.deps import act, current_actor, get_db
from app.web.templating import render

router = APIRouter()

KIND_LABELS = {"approved": "утверждена", "in_progress": "в работе на станке", "done": "станочная обработка выполнена",
               "preliminary": "предварительно (не утверждена)"}


def _bar_info(db: Session, plan, positions: dict[int, PositionRow]) -> dict[int, dict]:
    """Подписи, подсказки и состояние полос диаграммы Ганта."""
    info = {}
    for slot in plan.slots:
        pos = positions.get(slot.position_id)
        if pos is None:
            continue
        machine_ops = [o for o in operation_service.position_operations(db, pos.id)
                       if Operation(o.operation) in MACHINE_OPERATIONS]
        if FactKind.MACHINE in plan_service.fact_ends(db, pos.id):
            kind = "done"
        elif any(o.status in ("in_progress", "done") for o in machine_ops):
            kind = "in_progress"
        else:
            kind = "preliminary" if slot.preliminary else "approved"
        info[pos.id] = {
            "label": f"{pos.designation} ×{pos.quantity}", "kind": kind,
            "title": f"{pos.designation}\nЗаказ {pos.order.number}, {pos.quantity} шт.\n"
                     f"{slot.start:%d.%m %H:%M} — {slot.end:%d.%m %H:%M}\nСтатус: {KIND_LABELS[kind]}",
        }
    return info


@router.get("/plan")
def plan_page(request: Request, view: str = "gantt", db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.VIEW_PLAN)
    plan = plan_service.current_plan(db)
    load = plan_service.load_tables(db, plan)
    machines = [m for m in reference_service.list_machines(db) if m.is_cnc]
    table = load.weekly if view == "week" else load.daily
    columns = sorted({key for cells in table.values() for key in cells})
    positions = {p.id: p for p in db.scalars(select(PositionRow))}
    deviations = plan_service.deviations(db, plan)
    gantt = None
    if view == "gantt":
        gantt = build_gantt(plan.slots, [(m.code, m.model) for m in machines], datetime.now(),
                            _bar_info(db, plan, positions))
    return render(request, "plan.html", plan=plan, machines=machines, table=table, columns=columns,
                  view=view, positions=positions, deviations=deviations, gantt=gantt, kind_labels=KIND_LABELS)


@router.post("/machines/{code}/busy")
def set_busy(request: Request, code: str, busy_until: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    moment = datetime.fromisoformat(busy_until) if busy_until else None
    return act(request, db, "/plan", lambda: reference_service.set_busy_until(db, actor, code, moment),
               "Дата окончания текущих работ сохранена")


@router.get("/notifications")
def notifications(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.RECEIVE_NOTIFICATIONS)
    plan_service.sync_notifications(db)
    rows = plan_service.list_notifications(db, actor)
    response = render(request, "notifications.html", rows=rows)
    plan_service.mark_notifications_read(db, actor)
    return response
