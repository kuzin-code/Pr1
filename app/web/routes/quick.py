"""Быстрый расчёт по файлам: цена и срок по текущей загрузке без записи в базу."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.order_intake import IntakeError
from app.services import quick_service
from app.services.actor import ServiceError
from app.services.order_service import UploadedFile
from app.web.deps import act, current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


def _work_dir(request: Request):
    return request.app.state.settings.upload_dir


@router.get("/quick")
def quick_form(request: Request):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    return render(request, "quick.html", today=date.today().isoformat(), form={})


@router.post("/quick")
async def quick_calc(request: Request, quantity: str = Form("1"), desired_date: str = Form(""),
                     customer: str = Form(""), files: list[UploadFile] = File(default=[]),
                     db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    uploaded = [UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    form = {"quantity": quantity, "desired_date": desired_date, "customer": customer}
    try:
        result = quick_service.calculate(db, actor, uploaded, _work_dir(request), quantity=int(quantity or 1),
                                         desired_date=date.fromisoformat(desired_date) if desired_date else None,
                                         customer=customer.strip())
    except (IntakeError, ServiceError, ValueError) as err:
        return render(request, "quick.html", status_code=400, error=str(err), today=date.today().isoformat(),
                      form=form)
    return redirect(f"/quick/{result.token}")


@router.get("/quick/{token}")
def quick_result(request: Request, token: str):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    try:
        summary = quick_service.load(_work_dir(request), token)
    except ServiceError as err:
        flash(request, str(err), "error")
        return redirect("/quick")
    return render(request, "quick_result.html", s=summary, token=token)


@router.post("/quick/{token}/discard")
def quick_discard(request: Request, token: str):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    quick_service.discard(_work_dir(request), token)
    flash(request, "Расчёт удалён")
    return redirect("/quick")


@router.post("/quick/{token}/notify")
def quick_notify(request: Request, token: str, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, f"/quick/{token}",
               lambda: quick_service.notify_head(db, actor, _work_dir(request), token),
               "Сводка отправлена начальнику участка")


@router.post("/quick/{token}/launch")
def quick_launch(request: Request, token: str, db: Session = Depends(get_db)):
    actor = current_actor(request)
    try:
        order = quick_service.launch(db, actor, _work_dir(request), token, request.app.state.settings.upload_dir)
    except (IntakeError, ServiceError, ValueError) as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect(f"/quick/{token}")
    flash(request, f"Заказ {order.number} запущен: позиций {len(order.positions)}, расчёт выполнен.")
    return redirect(f"/orders/{order.id}")
