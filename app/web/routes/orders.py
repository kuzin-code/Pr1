"""Заказы: список, приём, карточка заказа (ФТ-01, ФТ-02, ФТ-16)."""
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import PositionStatus
from app.domain.status import order_done
from app.services import (approval_service, fact_service, intake_service, order_service, plan_service,
                          proposal_service, tech_service)
from app.services.proposal_docx import write_docx
from app.services.actor import ServiceError
from app.domain.order_intake import IntakeError
from app.parsers.order_list import write_template
from app.web.deps import act, current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


@router.get("/orders")
def orders_list(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.VIEW_ORDERS)
    plan = plan_service.current_plan(db)
    rows = []
    for order in order_service.list_orders(db):
        statuses = [PositionStatus(p.status) for p in order.positions]
        rows.append({"order": order, "deadline": plan_service.order_deadline(db, order.id, plan),
                     "done": order_done(statuses),
                     "preliminary": any(p.approved_seq is None for p in order.positions)})
    return render(request, "orders.html", rows=rows)


def _customers(db: Session) -> list[str]:
    """Заказчики прежних заказов — подсказка при вводе."""
    return sorted({o.customer for o in order_service.list_orders(db) if o.customer})


@router.get("/orders/new")
def order_new(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    return render(request, "order_new.html", today=date.today().isoformat(), customers=_customers(db), form={})


@router.get("/orders/template")
def order_template(request: Request):
    """Шаблон списка деталей заказа (Excel)."""
    require(current_actor(request).role, Action.VIEW_ORDERS)
    tmp = Path(tempfile.mkdtemp()) / "template.xlsx"
    write_template(tmp)
    return FileResponse(tmp, filename="Шаблон списка деталей.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.post("/orders")
async def order_create(request: Request, start_date: str = Form(""), number: str = Form(""), quantity: str = Form("1"),
                       customer: str = Form(""), desired_date: str = Form(""), opened_at: str = Form(""),
                       files: list[UploadFile] = File(default=[]), list_file: UploadFile | None = File(default=None),
                       db: Session = Depends(get_db)):
    """Приём заказа за минуту: все файлы одним полем; список Excel распознаётся по расширению."""
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    uploaded = [order_service.UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    if list_file is not None and list_file.filename:
        uploaded.insert(0, order_service.UploadedFile(list_file.filename, await list_file.read()))
    form = {"customer": customer, "desired_date": desired_date, "number": number, "start_date": start_date,
            "quantity": quantity}
    try:
        order = intake_service.quick_intake(
            db, actor, uploaded, request.app.state.settings.upload_dir, customer=customer,
            desired_date=date.fromisoformat(desired_date) if desired_date else None,
            start_date=date.fromisoformat(start_date) if start_date else None,
            number=number, quantity=int(quantity or 1))
    except (IntakeError, ServiceError, ValueError) as err:
        db.rollback()
        return render(request, "order_new.html", status_code=400, error=str(err), today=date.today().isoformat(),
                      customers=_customers(db), form=form)
    took = ""
    if opened_at.isdigit():
        seconds = max(0, int(datetime.now().timestamp() - int(opened_at) / 1000))
        took = f" Приём занял {seconds // 60} мин {seconds % 60} с." if seconds >= 60 else f" Приём занял {seconds} с."
    flash(request, f"Заказ {order.number} принят: позиций {len(order.positions)}, расчёт выполнен.{took}")
    for warning in getattr(order, "intake_warnings", []):
        flash(request, warning, "error")
    return redirect(f"/orders/{order.id}")


@router.get("/orders/{order_id}/proposal")
def proposal_page(request: Request, order_id: int, db: Session = Depends(get_db)):
    """Предварительный просмотр КП (лист А4); файл Word скачивается автоматически."""
    actor = current_actor(request)
    try:
        proposal = proposal_service.build_proposal(db, actor, order_id)
    except ServiceError as err:
        flash(request, str(err), "error")
        return redirect("/orders")
    return render(request, "proposal.html", p=proposal, order_id=order_id)


@router.get("/orders/{order_id}/proposal.docx")
def proposal_file(request: Request, order_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    proposal = proposal_service.build_proposal(db, actor, order_id, record_history=True)
    tmp = Path(tempfile.mkdtemp()) / "kp.docx"
    write_docx(proposal, tmp)
    name = re.sub(r'[\\/:*?"<>|]+', "-", f"{proposal.number} {proposal.customer}".strip())
    return FileResponse(tmp, filename=f"{name}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.get("/orders/{order_id}")
def order_page(request: Request, order_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.VIEW_ORDERS)
    order = order_service.get_order(db, order_id)
    plan = plan_service.current_plan(db)
    deviations = {d.position_id: d for d in plan_service.deviations(db, plan)}
    costs = {}
    for p in order.positions:
        if p.status == PositionStatus.DONE.value:
            costs[p.id] = fact_service.actual_cost(db, p.id)
    total = sum((plan_service.plan_calc(p) or {}).get("cost", {}).get("batch_cost", 0) for p in order.positions)
    unpaired = [p for p in order.positions if p.needs_link]
    return render(request, "order.html", order=order, plan=plan, deviations=deviations,
                  deadline=plan_service.order_deadline(db, order.id, plan), total_cost=total,
                  actual_costs=costs, unpaired=unpaired,
                  done=order_done([PositionStatus(p.status) for p in order.positions]))


@router.post("/positions/{position_id}/quantity")
def set_quantity(request: Request, position_id: int, quantity: int = Form(...), db: Session = Depends(get_db)):
    actor = current_actor(request)
    pos = order_service.get_position(db, position_id)
    back = f"/orders/{pos.order_id}"
    return act(request, db, back, lambda: order_service.set_quantity(db, actor, position_id, quantity))


@router.post("/orders/{order_id}/link")
def link(request: Request, order_id: int, position_id: int = Form(...), other_id: int = Form(...),
         db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, f"/orders/{order_id}",
               lambda: order_service.link_files(db, actor, position_id, other_id), "Файлы связаны")


@router.post("/orders/{order_id}/submit-all")
def submit_all(request: Request, order_id: int, db: Session = Depends(get_db)):
    """Технолог передаёт на утверждение все готовые позиции заказа."""
    actor = current_actor(request)

    def run():
        submitted, skipped = tech_service.submit_all(db, actor, order_id)
        flash(request, f"Передано на утверждение: {submitted}")
        for reason in skipped:
            flash(request, f"Не передана — {reason}", "error")

    return act(request, db, f"/orders/{order_id}", run)


@router.post("/orders/{order_id}/approve-all")
def approve_all(request: Request, order_id: int, db: Session = Depends(get_db)):
    """Начальник участка утверждает все ожидающие позиции заказа."""
    actor = current_actor(request)

    def run():
        flash(request, f"Утверждено позиций: {approval_service.approve_all(db, actor, order_id)}")

    return act(request, db, f"/orders/{order_id}", run)
