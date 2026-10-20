"""Быстрый расчёт по файлам: цена и срок по текущей загрузке без записи в базу."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.order_intake import IntakeError
from app.services import proposal_history, quick_service
from app.web.routes.proposals import storage as proposals_storage
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
                     customer: str = Form(""), quantities: str = Form(""),
                     files: list[UploadFile] = File(default=[]), db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    uploaded = [UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    form = {"quantity": quantity, "desired_date": desired_date, "customer": customer, "quantities": quantities}
    try:
        result = quick_service.calculate(db, actor, uploaded, _work_dir(request), quantity=int(quantity or 1),
                                         desired_date=date.fromisoformat(desired_date) if desired_date else None,
                                         customer=customer.strip(), quantities=quantities)
    except (IntakeError, ServiceError, ValueError) as err:
        return render(request, "quick.html", status_code=400, error=str(err), today=date.today().isoformat(),
                      form=form)
    return redirect(f"/quick/{result.token}")


@router.get("/quick/{token}")
def quick_result(request: Request, token: str, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.UPLOAD_ORDER)
    try:
        summary = quick_service.load(_work_dir(request), token)
    except ServiceError as err:
        flash(request, str(err), "error")
        return redirect("/quick")
    return render(request, "quick_result.html", s=summary, token=token, edit=request.query_params.get("edit"),
                  cnc_names={m["code"]: m["model"] for m in summary.get("cnc", [])},
                  similar=proposal_history.similar(db, [p["designation"] for p in summary.get("positions", [])]))


@router.post("/quick/{token}/proposal")
def quick_proposal(request: Request, token: str, db: Session = Depends(get_db)):
    actor = current_actor(request)
    try:
        row = proposal_history.create_from_quick(db, actor, _work_dir(request), token, proposals_storage(request))
    except ServiceError as err:
        flash(request, str(err), "error")
        return redirect(f"/quick/{token}")
    return redirect(f"/proposals/{row.id}?new=1")


def _num(value: str, cast=float):
    value = (value or "").strip().replace(",", ".")
    return cast(float(value)) if value else None


@router.post("/quick/{token}/machine")
def quick_machine(request: Request, token: str, designation: str = Form(...), machine: str = Form(...),
                  db: Session = Depends(get_db)):
    """Выбрать станок для позиции быстрого расчёта — пересчёт времени, стоимости и срока."""
    actor = current_actor(request)
    try:
        quick_service.update_position(db, actor, _work_dir(request), token, designation, {"machine": machine})
    except (IntakeError, ServiceError, ValueError) as err:
        flash(request, f"{designation}: {err}", "error")
    else:
        flash(request, f"{designation}: станок {machine} — пересчитано")
    return redirect(f"/quick/{token}")


@router.post("/quick/{token}/position")
async def quick_position(request: Request, token: str, db: Session = Depends(get_db)):
    """Правка параметров позиции быстрого расчёта и пересчёт."""
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    form = await request.form()
    designation = form.get("designation", "")
    try:
        summary = quick_service.load(_work_dir(request), token)
        current = next(p for p in summary["positions"] if p["designation"] == designation)
        values = {"quantity": _num(form.get("quantity"), int), "material": (form.get("material") or "").strip(),
                  "tolerance_grade": _num(form.get("tolerance_grade"), int),
                  "complexity": _num(form.get("complexity"), int), "technology": form.get("technology"),
                  "machine": form.get("machine")}
        dims = [_num(form.get(f"dim{i}")) for i in range(3)]
        if all(dims):
            values["dims"] = dims
        values = {k: v for k, v in values.items() if v not in (None, "")}
        code = form.get("extra_machine") or ""
        old = current["extra_machines"][0] if current["extra_machines"] else None
        if code and old and old["machine"] == code:
            extra = {"machine": code}
            for key in ("setup", "machining"):
                value = _num(form.get(f"extra_{key}"))
                if value is not None and abs(value - float(old.get(key) or 0)) > 1e-9:
                    extra[key] = value
            values["extra_machines"] = [extra]
        else:
            values["extra_machines"] = [{"machine": code}] if code else []
        if not old and not code:
            values.pop("extra_machines")
        values["route"] = {r["stage"]: _num(form.get(f"route_{r['stage']}")) for r in current["route"]
                           if _num(form.get(f"route_{r['stage']}")) is not None}
        quick_service.update_position(db, actor, _work_dir(request), token, designation, values)
    except (StopIteration, KeyError):
        flash(request, "Позиция расчёта не найдена", "error")
    except (IntakeError, ServiceError, ValueError) as err:
        flash(request, f"{designation}: {err}", "error")
    else:
        flash(request, f"{designation}: пересчитано")
    return redirect(f"/quick/{token}?edit={designation}")


@router.post("/quick/{token}/quantities")
def quick_quantities(request: Request, token: str, quantities: str = Form(""), db: Session = Depends(get_db)):
    """Ступени количества (1 / 10 / 50 шт.) — цена и срок по каждой позиции, пересчёт без записи в базу."""
    actor = current_actor(request)
    require(actor.role, Action.UPLOAD_ORDER)
    try:
        s = quick_service.set_quantities(db, actor, _work_dir(request), token, quantities)
    except (IntakeError, ServiceError, ValueError) as err:
        flash(request, str(err), "error")
    else:
        text = ", ".join(map(str, s["quantities"]))
        flash(request, f"Ступени количества: {text} — пересчитано" if text else "Ступени количества убраны")
    return redirect(f"/quick/{token}")


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
