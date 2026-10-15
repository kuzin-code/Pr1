"""Заказы: список, приём, карточка заказа (ФТ-01, ФТ-02, ФТ-16)."""
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import PositionStatus
from app.domain.status import order_done
from app.services import fact_service, order_service, plan_service
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


@router.get("/orders/new")
def order_new(request: Request):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    return render(request, "order_new.html", today=date.today().isoformat())


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
                       files: list[UploadFile] = File(default=[]), list_file: UploadFile | None = File(default=None),
                       db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    uploaded = [order_service.UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    upload_dir = request.app.state.settings.upload_dir
    try:
        start = date.fromisoformat(start_date) if start_date else None
        if list_file is not None and list_file.filename:
            listed = order_service.UploadedFile(list_file.filename, await list_file.read())
            order, warnings = order_service.create_order_from_list(db, actor, start, listed, uploaded, upload_dir,
                                                                   number.strip() or None)
            for warning in warnings:
                flash(request, warning, "error")
            return redirect(f"/orders/{order.id}")
        if not uploaded:
            raise ServiceError("Выберите файлы STEP и PDF или список деталей (Excel)")
        qty = int(quantity or 1)
        order = order_service.create_order(db, actor, start, uploaded, upload_dir,
                                           number.strip() or None, quantity=qty)
    except (IntakeError, ServiceError, ValueError) as err:
        db.rollback()
        return render(request, "order_new.html", status_code=400, error=str(err),
                      today=start_date or date.today().isoformat())
    return redirect(f"/orders/{order.id}")


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
