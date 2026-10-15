"""Справочник оборудования: добавление и правка станков (вносят IT и технолог)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.services import reference_service as ref
from app.web.deps import current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


async def _form_fields(request: Request) -> tuple[dict, str]:
    form = await request.form()
    fields = {name: (form.getlist(name) if kind == "stages" else str(form.get(name, "")))
              for name, _, kind, *_ in ref.MACHINE_FIELDS}
    fields["is_cnc"] = form.get("is_cnc") == "on"
    return fields, str(form.get("reason", ""))


@router.get("/machines/new")
def machine_new(request: Request):
    require(current_actor(request).role, Action.EDIT_REFERENCE)
    return render(request, "machine_form.html", machine=None, fields=ref.MACHINE_FIELDS, gaps=[])


@router.post("/machines")
async def machine_create(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_REFERENCE)
    fields, _ = await _form_fields(request)
    fields["code"] = str((await request.form()).get("code", ""))
    try:
        ref.create_machine(db, actor, fields)
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect("/machines/new")
    flash(request, "Станок добавлен в справочник")
    return redirect("/reference")


@router.get("/machines/{code}/edit")
def machine_edit(request: Request, code: str, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.EDIT_REFERENCE)
    machine = ref.get_machine(db, code)
    return render(request, "machine_form.html", machine=machine, fields=ref.MACHINE_FIELDS,
                  gaps=ref.machine_gaps(machine))


@router.post("/machines/{code}")
async def machine_update(request: Request, code: str, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_REFERENCE)
    fields, reason = await _form_fields(request)
    try:
        ref.update_machine(db, actor, code, fields, reason)
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect(f"/machines/{code}/edit")
    flash(request, "Характеристики сохранены")
    return redirect("/reference")
