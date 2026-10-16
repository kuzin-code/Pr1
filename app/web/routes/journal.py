"""Журнал калибровки: список, импорт/экспорт Excel, карточка детали с правкой (ФТ-25)."""
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.domain.access import Action, require
from app.domain.calibration import k_for
from app.domain.enums import CountMode, Stage, StageGroup
from app.services import journal_service as js
from app.services import reference_service as ref
from app.services.actor import ServiceError
from app.services.file_check import check_entry_files
from app.services.history_service import list_history
from app.parsers.journal_excel import write_journal_template
from app.services.order_service import UploadedFile
from app.storage.convert import entry_from_row
from app.web.deps import act, current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


def _storage(request: Request) -> Path:
    return Path(request.app.state.settings.upload_dir) / "journal"


@router.get("/journal")
def journal_page(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    rows = {r.part_code: r for r in js.journal_rows(db)}
    entries = ref.journal_entries(db)
    ks = {e.part_code: {g: k_for(e, g) for g in (StageGroup.MACHINE, StageGroup.BENCH)} for e in entries}
    files = {code: check_entry_files(row) for code, row in rows.items()}
    missing = sum(1 for status in files.values() if not all(s.ok for s in status))
    unconfirmed = sum(1 for r in rows.values() if r.confirmed is False)
    return render(request, "journal.html", entries=entries, rows=rows, ks=ks, version=ref.journal_version(db),
                  unconfirmed=unconfirmed, drawings_dir=request.app.state.settings.drawings_dir,
                  files=files, missing=missing,
                  machine_group=StageGroup.MACHINE, bench_group=StageGroup.BENCH)


@router.get("/journal/export")
def journal_export(request: Request, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    tmp = Path(tempfile.mkdtemp()) / "journal.xlsx"
    ref.export_journal_file(db, tmp)
    return FileResponse(tmp, filename="Журнал_калибровки.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.get("/journal/template")
def journal_template(request: Request):
    """Пустой журнал калибровки с подсказкой по заполнению."""
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    tmp = Path(tempfile.mkdtemp()) / "template.xlsx"
    write_journal_template(tmp)
    return FileResponse(tmp, filename="Шаблон журнала калибровки.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@router.post("/journal/import")
async def journal_import(request: Request, file: UploadFile = File(...), files: list[UploadFile] = File(default=[]),
                         replace: str = Form(""), db: Session = Depends(get_db)):
    """Журнал из Excel и сразу STEP/PDF деталей — файлы привязываются по обозначению."""
    actor = current_actor(request)
    require(actor.role, Action.IMPORT_JOURNAL)
    tmp = Path(tempfile.mkdtemp()) / "upload.xlsx"
    tmp.write_bytes(await file.read())
    uploaded = [UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]

    def run():
        try:
            report = js.import_journal_with_files(db, actor, tmp, uploaded, _storage(request), replace == "on")
        finally:
            tmp.unlink(missing_ok=True)
        flash(request, f"Журнал загружен: деталей {report.imported}, файлов прикреплено {report.attached}")
        for warning in report.warnings:
            flash(request, warning, "error")

    return act(request, db, "/journal", run)


@router.post("/journal/new")
async def journal_new(request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Новая деталь по STEP+PDF: данные читаются из файлов, время предлагается по аналогу."""
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    uploaded = [UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]
    try:
        row = js.create_entry_from_files(db, actor, uploaded, _storage(request))
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect("/journal")
    flash(request, "Деталь добавлена: проверьте характеристики и время операций")
    return redirect(f"/journal/{row.id}")


@router.post("/journal/import-folder")
def journal_import_folder(request: Request, db: Session = Depends(get_db)):
    """Подхватить пары STEP+PDF из папки КД проекта."""
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    folder = request.app.state.settings.drawings_dir

    def run():
        report = js.import_folder(db, actor, folder, _storage(request))
        flash(request, f"Папка КД: новых деталей {len(report.created)}, прикреплены файлы {len(report.files_attached)}, "
                       f"уже в журнале {len(report.skipped)}")
        if report.created:
            flash(request, "Время новых деталей предложено системой — проверьте и сохраните его в карточках", "error")
        for name in report.unpaired:
            flash(request, f"Нет пары STEP+PDF: {name}", "error")
        for note in report.solidworks:
            flash(request, note, "error")
        for err in report.errors:
            flash(request, err, "error")

    return act(request, db, "/journal", run)


@router.post("/journal/{entry_id:int}/confirm")
def entry_confirm(request: Request, entry_id: int, db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    return act(request, db, f"/journal/{entry_id}", lambda: js.confirm_times(db, actor, entry_id),
               "Время операций отмечено как проверенное")


@router.get("/journal/{entry_id:int}")
def entry_page(request: Request, entry_id: int, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    row = js.entry_row(db, entry_id)
    entry = entry_from_row(row)
    return render(request, "journal_entry.html", row=row, entry=entry, files=check_entry_files(row),
                  stages={s.stage: s for s in entry.stages}, all_stages=list(Stage), modes=list(CountMode),
                  ks={g: k_for(entry, g) for g in (StageGroup.MACHINE, StageGroup.BENCH)},
                  machines=ref.list_machines(db), machine_group=StageGroup.MACHINE, bench_group=StageGroup.BENCH,
                  history=list_history(db, entity="journal", entity_id=row.part_code, limit=50))


@router.post("/journal/{entry_id:int}/update")
def entry_update(request: Request, entry_id: int, part_code: str = Form(...), name: str = Form(""),
                 machine_code: str = Form(...), material: str = Form(""), dim_a: str = Form(...),
                 dim_b: str = Form(...), dim_c: str = Form(...), tolerance_grade: str = Form(...),
                 complexity: str = Form(...), technology: str = Form("milling"), reason: str = Form(""),
                 db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    fields = {"part_code": part_code, "name": name, "machine_code": machine_code, "material": material,
              "dims": [v.replace(",", ".") for v in (dim_a, dim_b, dim_c)], "tolerance_grade": tolerance_grade,
              "complexity": complexity, "technology": technology}
    return act(request, db, f"/journal/{entry_id}", lambda: js.update_entry(db, actor, entry_id, fields, reason),
               "Характеристики сохранены")


@router.post("/journal/{entry_id:int}/stages")
async def entry_stages(request: Request, entry_id: int, db: Session = Depends(get_db)):
    """Время каждой операции; пустое поле — операции нет в маршруте детали."""
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    form = await request.form()

    def run():
        stages = {}
        for stage in Stage:
            raw = str(form.get(f"hours_{stage.value}", "")).replace(",", ".").strip()
            try:
                hours = float(raw) if raw else None
            except ValueError:
                raise ServiceError(f"{stage.label}: время должно быть числом") from None
            stages[stage] = (hours, CountMode(form.get(f"mode_{stage.value}", "piece")))
        js.set_stage_times(db, actor, entry_id, stages, str(form.get("reason", "")))

    return act(request, db, f"/journal/{entry_id}", run, "Время операций сохранено")


@router.post("/journal/{entry_id:int}/measurement")
def entry_measurement(request: Request, entry_id: int, group: str = Form(...), calc_hours: str = Form(...),
                      actual_hours: str = Form(...), reason: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)

    def run():
        try:
            calc, actual = float(calc_hours.replace(",", ".")), float(actual_hours.replace(",", "."))
        except ValueError:
            raise ServiceError("Время замера должно быть числом") from None
        js.add_manual_measurement(db, actor, entry_id, StageGroup(group), calc, actual, reason)

    return act(request, db, f"/journal/{entry_id}", run, "Замер добавлен, k пересчитан")


@router.post("/journal/{entry_id:int}/files")
async def entry_files(request: Request, entry_id: int, files: list[UploadFile] = File(...),
                      db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    uploaded = [UploadedFile(f.filename or "", await f.read()) for f in files if f.filename]

    def run():
        for warning in js.attach_files(db, actor, entry_id, uploaded, _storage(request)):
            flash(request, f"Проверьте файл: {warning}", "error")

    return act(request, db, f"/journal/{entry_id}", run, "Файлы прикреплены")


@router.get("/journal/{entry_id:int}/model")
def entry_model(request: Request, entry_id: int, db: Session = Depends(get_db)):
    """3D-просмотр модели STEP детали журнала в браузере."""
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    row = js.entry_row(db, entry_id)
    return render(request, "model.html", title=row.part_code, step_url=f"/journal/{entry_id}/file/step",
                  pdf_url=f"/journal/{entry_id}/file/pdf" if row.pdf_path else None,
                  back_url=f"/journal/{entry_id}", back_label="Карточка детали")


@router.get("/journal/{entry_id:int}/file/{kind}")
def entry_file(request: Request, entry_id: int, kind: str, db: Session = Depends(get_db)):
    require(current_actor(request).role, Action.VIEW_JOURNAL)
    row = js.entry_row(db, entry_id)
    path, name = (row.pdf_path, row.pdf_name) if kind == "pdf" else (row.step_path, row.step_name)
    if kind not in ("pdf", "step") or not path or not Path(path).exists():
        return render(request, "error.html", status_code=404, title="Файл не найден", message="")
    return FileResponse(path, filename=name, media_type="application/pdf" if kind == "pdf" else "application/step",
                        content_disposition_type="inline" if kind == "pdf" else "attachment")


@router.post("/journal/{entry_id:int}/delete")
def entry_delete(request: Request, entry_id: int, reason: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    require(actor.role, Action.EDIT_JOURNAL)
    return act(request, db, "/journal", lambda: js.delete_entry(db, actor, entry_id, reason), "Деталь удалена из журнала")
