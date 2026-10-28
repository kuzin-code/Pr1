"""Справочники, ставки, журнал калибровки, история (ФТ-24, ФТ-25)."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.domain.access import Action, require
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
def set_blank_rules(request: Request, sheet_side_allowance_mm: str = Form(...), sheet_thickness_allowance_mm: str = Form(...),
                    bar_diameter_allowance_mm: str = Form(...), bar_length_allowance_mm: str = Form(...),
                    size_step_mm: str = Form(...), sheet_thicknesses_mm: str = Form(...),
                    round_diameters_mm: str = Form(...), reason: str = Form(""), db: Session = Depends(get_db)):
    """Припуски и стандартные ряды заготовки (кто ведёт справочник; с причиной — в историю)."""
    actor = current_actor(request)
    values = dict(sheet_side_allowance_mm=sheet_side_allowance_mm, sheet_thickness_allowance_mm=sheet_thickness_allowance_mm,
                  bar_diameter_allowance_mm=bar_diameter_allowance_mm, bar_length_allowance_mm=bar_length_allowance_mm,
                  size_step_mm=size_step_mm, sheet_thicknesses_mm=sheet_thicknesses_mm, round_diameters_mm=round_diameters_mm)
    return act(request, db, "/reference#blank-rules", lambda: blank_service.set_rules(db, actor, values, reason),
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
