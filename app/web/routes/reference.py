"""Справочники, ставки, журнал калибровки, история (ФТ-24, ФТ-25)."""
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.calibration import k_for
from app.domain.enums import StageGroup
from app.services import reference_service as ref
from app.services.history_service import list_history
from app.web.deps import act, current_actor, get_db
from app.web.templating import render

router = APIRouter()


@router.get("/reference")
def reference_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_REFERENCE)
    return render(request, "reference.html", machines=ref.list_machines(db), rates=ref.get_rates(db),
                  scale=ref.complexity_scale(db))


@router.post("/rates")
def set_rates(request: Request, machine: str = Form(...), bench: str = Form(...), reason: str = Form(""),
              db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_RATES)
    return act(request, db, "/reference", lambda: ref.set_rates(
        db, actor, float(machine.replace(",", ".")), float(bench.replace(",", ".")), reason), "Ставки изменены")


@router.get("/journal")
def journal_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    entries = ref.journal_entries(db)
    ks = {e.part_code: {g: k_for(e, g) for g in (StageGroup.MACHINE, StageGroup.BENCH)} for e in entries}
    return render(request, "journal.html", entries=entries, ks=ks, version=ref.journal_version(db),
                  machine_group=StageGroup.MACHINE, bench_group=StageGroup.BENCH)


@router.get("/journal/export")
def journal_export(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    tmp = Path(tempfile.mkdtemp()) / "journal.xlsx"
    ref.export_journal_file(db, tmp)
    return FileResponse(tmp, filename="Журнал_калибровки.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.post("/journal/import")
async def journal_import(request: Request, file: UploadFile = File(...), replace: str = Form(""),
                         db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.IMPORT_JOURNAL)
    tmp = Path(tempfile.mkdtemp()) / "upload.xlsx"
    tmp.write_bytes(await file.read())

    def run():
        try:
            ref.import_journal_file(db, actor, tmp, replace=replace == "on")
        finally:
            tmp.unlink(missing_ok=True)

    return act(request, db, "/journal", run, "Журнал загружен")


@router.get("/history")
def history_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_HISTORY)
    return render(request, "history.html", history=list_history(db, limit=300))
