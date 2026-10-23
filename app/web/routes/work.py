"""Окно специалиста: очередь операций своего оборудования, кнопки «Начал» / «Закончил»."""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.downtime import REASON_LABELS, needs_reason
from app.domain.operations import MACHINE_OPERATIONS, operations_for_role
from app.services import downtime_service
from app.services import operation_service as ops
from app.services import reference_service as ref
from app.storage.orm import OperationRow, PositionRow
from app.web.deps import act, current_actor, get_db, redirect
from app.web.templating import render

router = APIRouter()


@router.get("/work")
def work_page(request: Request, machine: str | None = None, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.DO_OPERATIONS)
    if machine is not None:  # выбор «моего станка» запоминается в сессии общей учётной записи
        request.session["machine"] = machine
    selected = request.session.get("machine") or ""
    my_ops = operations_for_role(actor.role)
    machines = [m for m in ref.list_machines(db) if set(m.operations or []) & {o.value for o in my_ops}]
    rows = ops.queue(db, actor, selected or None)
    positions = {r.position_id: db.get(PositionRow, r.position_id) for r in rows}
    moment = datetime.now()
    # Идёт дольше плана × порог — список причин открыт сразу и помечен «обязательно».
    overdue = {r.id for r in rows if r.started_at and needs_reason(r.planned_hours, ops.elapsed_hours(r, moment))}
    return render(request, "work.html", rows=rows, positions=positions, machines=machines, selected=selected,
                  show_machine_filter=bool(my_ops & MACHINE_OPERATIONS) and len(machines) > 1,
                  now=moment.strftime("%Y-%m-%dT%H:%M"), overdue=overdue, reasons=REASON_LABELS,
                  shift_machine=_shift_machine(machines, selected))


def _shift_machine(machines, selected: str) -> str:
    """Станок для ссылки «Сменное задание»: выбранный станок с ЧПУ или первый из своих."""
    cnc = [m.code for m in machines if m.is_cnc]
    return selected if selected in cnc else (cnc[0] if cnc else "")


def _moment(at: str) -> datetime | None:
    return datetime.fromisoformat(at) if at else None


@router.post("/operations/{op_id}/start")
def op_start(request: Request, op_id: int, at: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, "/work", lambda: ops.start(db, actor, op_id, _moment(at)), "Операция начата")


@router.post("/operations/{op_id}/finish")
def op_finish(request: Request, op_id: int, at: str = Form(""), reason: str = Form(""), note: str = Form(""),
              db: Session = Depends(get_db)):
    """«Закончил»; при факте больше плана × порог — с причиной отклонения из справочника."""
    actor = current_actor(request)
    return act(request, db, "/work", lambda: ops.finish(db, actor, op_id, _moment(at), reason, note),
               "Операция выполнена")


@router.post("/operations/{op_id}/reason")
def op_reason(request: Request, op_id: int, reason: str = Form(""), note: str = Form(""),
              comment: str = Form(""), db: Session = Depends(get_db)):
    """Мастер исправляет причину отклонения выполненной операции — с комментарием (в историю)."""
    actor = current_actor(request)
    row = db.get(OperationRow, op_id)
    back = f"/positions/{row.position_id}#operations" if row else "/work"
    return act(request, db, back, lambda: downtime_service.set_reason(db, actor, op_id, reason, note, comment),
               "Причина отклонения исправлена")


@router.get("/facts")
def facts_page():
    """Прежняя страница групповых отметок заменена окном операций."""
    return redirect("/work")

