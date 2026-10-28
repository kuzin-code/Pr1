"""Справочники, ставки, журнал калибровки, история (ФТ-24, ФТ-25)."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.blank import ROUND_GROUPS
from app.services import blank_service
from app.services import reference_service as ref
from app.services.history_service import list_history
from app.web.deps import act, current_actor, get_db
from app.web.templating import render

router = APIRouter()


@router.get("/reference")
def reference_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_REFERENCE)
    machines = ref.list_machines(db)
    return render(request, "reference.html", machines=machines, rates=ref.get_rates(db),
                  scale=ref.complexity_scale(db), gaps={m.code: ref.machine_gaps(m) for m in machines},
                  blank_rules=blank_service.rules(db))


@router.post("/reference/blank-rules")
async def set_blank_rules(request: Request, db: Session = Depends(get_db)):
    """Припуски на сторону и стандартные ряды заготовки (кто ведёт справочник; с причиной — в историю)."""
    actor = current_actor(request)
    form = await request.form()
    values = {k: str(form.get(k, "")) for k in blank_service.ALLOWANCE_FIELDS + ("sheet_thicknesses_mm",)}
    values["round_diameters_mm"] = {g: str(form.get(f"round_{g}", "")) for g in ROUND_GROUPS}
    return act(request, db, "/reference#blank-rules",
               lambda: blank_service.set_rules(db, actor, values, str(form.get("reason", ""))),
               "Припуски заготовки изменены")


@router.post("/rates")
def set_rates(request: Request, machine: str = Form(...), bench: str = Form(...), reason: str = Form(""),
              db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_RATES)
    return act(request, db, "/reference", lambda: ref.set_rates(
        db, actor, float(machine.replace(",", ".")), float(bench.replace(",", ".")), reason), "Ставки изменены")


@router.get("/history")
def history_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_HISTORY)
    return render(request, "history.html", history=list_history(db, limit=300))
