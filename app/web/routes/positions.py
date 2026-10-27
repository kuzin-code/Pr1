"""Карточка позиции: данные, аналог, маршрут, станок, расчёт, утверждение, факт."""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.domain.access import Action, can, require
from app.domain.enums import PositionStatus, Stage
from app.domain.status import is_editable
from app.services import (approval_service, dfm_service, fact_service, journal_service, operation_service,
                          order_service, plan_service, quick_apply, tech_service)
from app.services.actor import ServiceError
from app.services.history_service import list_history
from app.services.reference_service import complexity_scale, domain_machines, journal_entries
from app.web.deps import act, current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


def _back(position_id: int) -> str:
    return f"/positions/{position_id}"


def _local(url: str) -> bool:
    """Переход после действия — только на страницу этого приложения (не «//сайт» и не адрес другого сайта)."""
    return url.startswith("/") and not url.startswith(("//", "/\\"))


@router.get("/positions/{position_id}")
def position_page(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.VIEW_ORDERS)
    pos = order_service.get_position(db, position_id)
    analogs, allowed, suggested, problem = [], [], None, None
    try:
        analogs = tech_service.analog_candidates(db, pos.id)
        allowed, suggested = tech_service.machine_options(db, pos.id)
    except ServiceError as err:
        problem = str(err)
    plan = plan_service.current_plan(db)
    deviation = next((d for d in plan_service.deviations(db, plan) if d.position_id == pos.id), None)
    actual = fact_service.actual_cost(db, pos.id) if pos.approved_calc else None
    return render(
        request, "position.html", pos=pos, analogs=analogs, allowed=allowed, suggested=suggested,
        problem=problem, scale=complexity_scale(db),
        journal=[e for e in journal_entries(db) if e.technology == ((pos.data or {}).get("technology") or "milling")], editable=is_editable(PositionStatus(pos.status)),
        calc=plan_service.plan_calc(pos), planned=plan.positions.get(pos.id), deviation=deviation,
        actual=actual, history=list_history(db, entity="position", entity_id=pos.id, limit=50),
        facts=plan_service.fact_ends(db, pos.id), operations=operation_service.position_operations(db, pos.id),
        last_mark=operation_service.last_mark(db, pos.id),
        cnc_machines=[m for m in domain_machines(db) if m.is_cnc],
        machine_names={m.code: m.model for m in domain_machines(db)},
    )


@router.get("/positions/{position_id}/file/{kind}")
def position_file(request: Request, position_id: int, kind: str, db: Session = Depends(get_db)):
    """Открыть чертёж PDF или модель STEP позиции (ФТ-03, ФТ-04); специалисту — по своим операциям."""
    operation_service.require_position_files(db, current_actor(request), position_id)
    pos = order_service.get_position(db, position_id)
    path, name = (pos.pdf_path, pos.pdf_name) if kind == "pdf" else (pos.step_path, pos.step_name)
    if kind not in ("pdf", "step") or not path or not Path(path).exists():
        return render(request, "error.html", status_code=404, title="Файл не найден", message="")
    media = "application/pdf" if kind == "pdf" else "application/step"
    disposition = "inline" if kind == "pdf" else "attachment"
    return FileResponse(path, media_type=media, filename=name, content_disposition_type=disposition)


@router.get("/positions/{position_id}/model")
def position_model(request: Request, position_id: int, db: Session = Depends(get_db)):
    """3D-просмотр модели STEP позиции заказа (ФТ-03: технолог смотрит нюансы детали; специалист — у станка)."""
    actor = current_actor(request)
    operation_service.require_position_files(db, actor, position_id)
    pos = order_service.get_position(db, position_id)
    back = ((f"/positions/{position_id}", "Карточка позиции") if can(actor.role, Action.VIEW_ORDERS)
            else ("/work", "Мои операции"))
    return render(request, "model.html", title=pos.designation, step_url=f"/positions/{position_id}/file/step",
                  pdf_url=f"/positions/{position_id}/file/pdf" if pos.pdf_path else None,
                  back_url=back[0], back_label=back[1])


@router.post("/positions/{position_id}/files")
async def attach_files(request: Request, position_id: int, files: list[UploadFile] = File(default=[]),
                       db: Session = Depends(get_db)):
    """Прикрепить или заменить STEP/PDF позиции (мастер, технолог)."""
    actor = current_actor(request)
    uploaded = [order_service.UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    return act(request, db, _back(position_id), lambda: order_service.attach_position_files(
        db, actor, position_id, uploaded, request.app.state.settings.upload_dir), "Файлы прикреплены, расчёт обновлён")


@router.post("/positions/{position_id}/dfm/{code}/dismiss")
def dismiss_dfm(request: Request, position_id: int, code: str, reason: str = Form(""), db: Session = Depends(get_db)):
    """Технолог снимает предупреждение проверки технологичности — с причиной (в историю)."""
    actor = current_actor(request)
    return act(request, db, _back(position_id) + "#dfm",
               lambda: dfm_service.dismiss(db, actor, position_id, code, reason), "Предупреждение снято")


@router.post("/positions/{position_id}/undo")
def undo_mark(request: Request, position_id: int, reason: str = Form(""), db: Session = Depends(get_db)):
    """Мастер отменяет последнюю ошибочную отметку «Начал» / «Закончил»."""
    actor = current_actor(request)
    return act(request, db, _back(position_id),
               lambda: operation_service.undo_last(db, actor, position_id, reason), "Отметка отменена")


@router.post("/positions/{position_id}/data")
def update_data(request: Request, position_id: int, dim_a: str = Form(""), dim_b: str = Form(""),
                dim_c: str = Form(""), volume: str = Form(""), material: str = Form(""),
                tolerance_grade: str = Form(""), threads: str = Form(""), technology: str = Form("milling"),
                reason: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)

    def run():
        changes = {"material": material, "threads": threads, "technology": technology,
                   "volume": volume.replace(",", ".") or None}
        if dim_a and dim_b and dim_c:
            changes["dims"] = [v.replace(",", ".") for v in (dim_a, dim_b, dim_c)]
        if tolerance_grade:
            changes["tolerance_grade"] = tolerance_grade
        tech_service.update_data(db, actor, position_id, changes, reason)

    return act(request, db, _back(position_id), run, "Данные сохранены")


@router.post("/positions/{position_id}/complexity")
def set_complexity(request: Request, position_id: int, complexity: int = Form(...), reason: str = Form(""),
                   db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id),
               lambda: tech_service.set_complexity(db, actor, position_id, complexity, reason))


@router.post("/positions/{position_id}/cooperation")
def set_cooperation(request: Request, position_id: int, cooperation: str = Form(""),
                    db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id),
               lambda: tech_service.set_cooperation(db, actor, position_id, cooperation == "on"))


@router.post("/positions/{position_id}/analog")
def choose_analog(request: Request, position_id: int, code: str = Form(...), db: Session = Depends(get_db)):
    actor = current_actor(request)
    if code == "__none__":
        fn = lambda: tech_service.choose_no_analog(db, actor, position_id)  # noqa: E731
    else:
        fn = lambda: tech_service.choose_analog(db, actor, position_id, code)  # noqa: E731
    return act(request, db, _back(position_id), fn)


@router.post("/positions/{position_id}/route")
def edit_route(request: Request, position_id: int, stage: str = Form(...), hours: str = Form("0"),
               count_mode: str = Form("piece"), reason: str = Form(""), remove: str = Form(""),
               db: Session = Depends(get_db)):
    actor = current_actor(request)
    if remove:
        fn = lambda: tech_service.remove_route_stage(db, actor, position_id, Stage(stage), reason)  # noqa: E731
    else:
        fn = lambda: tech_service.edit_route_stage(  # noqa: E731
            db, actor, position_id, Stage(stage), float(hours.replace(",", ".") or 0), count_mode, reason)
    return act(request, db, _back(position_id), fn)


@router.post("/positions/{position_id}/machine")
def assign_machine(request: Request, position_id: int, code: str = Form(...), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.assign_machine(db, actor, position_id, code))


@router.post("/positions/{position_id}/extra-machine")
def add_machine(request: Request, position_id: int, code: str = Form(...), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.add_machine(db, actor, position_id, code),
               f"Станок {code} добавлен в техпроцесс: время и стоимость пересчитаны")


@router.post("/positions/{position_id}/extra-machine/{code}/time")
def extra_machine_time(request: Request, position_id: int, code: str, setup: str = Form("0"),
                       machining: str = Form("0"), reason: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.set_extra_machine_time(
        db, actor, position_id, code, float(setup.replace(",", ".") or 0), float(machining.replace(",", ".") or 0),
        reason))


@router.post("/positions/{position_id}/extra-machine/{code}/remove")
def remove_machine(request: Request, position_id: int, code: str, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.remove_machine(db, actor, position_id, code))


@router.post("/positions/{position_id}/delete")
def delete_position(request: Request, position_id: int, reason: str = Form(""), back: str = Form(""),
                    db: Session = Depends(get_db)):
    """Удалить расчёт позиции до утверждения (с причиной)."""
    actor = current_actor(request)
    try:
        pos = order_service.get_position(db, position_id)
        name = pos.designation
        order_id = order_service.delete_position(db, actor, position_id, reason)
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect(back if _local(back) else _back(position_id))
    flash(request, f"Расчёт «{name}» удалён" + ("" if order_id else "; в заказе не осталось позиций — заказ удалён"))
    if back.startswith("/dashboard"):
        return redirect(back)
    return redirect(f"/orders/{order_id}" if order_id else "/orders")


@router.post("/positions/{position_id}/calculate")
def calculate(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.calculate(db, actor, position_id),
               "Расчёт выполнен")


@router.post("/positions/{position_id}/auto")
def auto_recalculate(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.auto_recalculate(db, actor, position_id),
               "Расчёт выполнен заново")


@router.post("/positions/{position_id}/journal")
def add_to_journal(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    return act(request, db, _back(position_id), lambda: journal_service.add_to_journal(db, actor, position_id),
               "Деталь внесена в журнал калибровки")


@router.post("/positions/{position_id}/submit")
def submit(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: tech_service.submit(db, actor, position_id),
               "Позиция передана на утверждение")


@router.post("/positions/{position_id}/approve")
def approve(request: Request, position_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id), lambda: approval_service.approve(db, actor, position_id),
               "Позиция утверждена")


@router.post("/positions/{position_id}/quick-proposal/{decision}")
def quick_proposal(request: Request, position_id: int, decision: str, reason: str = Form(""),
                   db: Session = Depends(get_db)):
    """Технолог принимает или отклоняет предложение из быстрого расчёта (с причиной)."""
    actor = current_actor(request)
    if decision not in ("accept", "reject"):
        return redirect(_back(position_id))
    fn = quick_apply.accept_proposal if decision == "accept" else quick_apply.reject_proposal
    return act(request, db, _back(position_id), lambda: fn(db, actor, position_id, reason),
               "Предложение принято — расчёт обновлён" if decision == "accept" else "Предложение отклонено")


@router.post("/positions/{position_id}/return")
def return_position(request: Request, position_id: int, reason: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, _back(position_id),
               lambda: approval_service.return_position(db, actor, position_id, reason),
               "Позиция возвращена на пересчёт")
