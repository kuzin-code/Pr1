"""Ведение журнала калибровки технологом: добавление детали (из позиции или по STEP+PDF),
правка характеристик и времени операций, ручные замеры, файлы, удаление (ФТ-25)."""
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.analog import pick_analog
from app.domain.calibration import add_measurement
from app.domain.complexity import estimate_complexity
from app.domain.enums import CountMode, Stage, StageGroup
from app.domain.models import CalibrationEntry, Dimensions, PartFeatures, StageNorm
from app.domain.order_intake import designation, file_kind, pair_files
from app.domain.route import route_from_list
from app.services.actor import Actor, ServiceError
from app.services.auto_service import MANUAL_ROUTE
from app.services.calc_service import DEFAULT_GRADE, position_features
from app.services.file_check import check_entry_files, journal_code, name_warning, unreadable_reason
from app.services.history_service import record
from app.services.order_service import UploadedFile, get_position, read_documents
from app.services.reference_service import (
    bump_journal_version,
    get_machine,
    journal_entries,
    preferred_machines,
)
from app.storage.convert import entry_from_row, entry_to_row
from app.storage.orm import JournalRow

TECHNOLOGIES = {"milling", "turning"}


def journal_rows(session: Session) -> list[JournalRow]:
    return list(session.scalars(select(JournalRow).order_by(JournalRow.part_code)))


def entry_row(session: Session, entry_id: int) -> JournalRow:
    row = session.get(JournalRow, entry_id)
    if row is None:
        raise ServiceError("Деталь журнала не найдена")
    return row


def entry_row_by_code(session: Session, code: str) -> JournalRow:
    row = session.scalar(select(JournalRow).where(JournalRow.part_code == code))
    if row is None:
        raise ServiceError(f"Деталь «{code}» отсутствует в журнале калибровки")
    return row


def _ensure_new(session: Session, code: str) -> None:
    if session.scalar(select(JournalRow.id).where(JournalRow.part_code == code)):
        raise ServiceError(f"Деталь «{code}» уже есть в журнале калибровки")


def _save(session: Session, actor: Actor, row: JournalRow, field: str, old, new, reason: str = "") -> None:
    bump_journal_version(session)
    record(session, actor, "journal", row.part_code, field, old, new, reason)
    session.commit()


def add_to_journal(session: Session, actor: Actor, position_id: int) -> CalibrationEntry:
    """Рассчитанная позиция вносится с маршрутом и временем этапов; замеры появятся по факту."""
    require(actor.role, Action.EDIT_JOURNAL)
    pos = get_position(session, position_id)
    if not (pos.current_calc or pos.approved_calc):
        raise ServiceError("Позиция ещё не рассчитана")
    if not pos.machine_code:
        raise ServiceError("Не назначен станок")
    code = journal_code(pos.designation)
    _ensure_new(session, code)
    features = position_features(pos)
    data = pos.data or {}
    entry = CalibrationEntry(
        part_code=code, name=code, machine_code=pos.machine_code, material=data.get("material") or "",
        dims=Dimensions(*features.dims.as_list()), tolerance_grade=features.tolerance_grade,
        complexity=features.complexity, stages=route_from_list(pos.route), technology=features.technology,
    )
    row = entry_to_row(entry)
    row.step_name, row.step_path, row.pdf_name, row.pdf_path = pos.step_name, pos.step_path, pos.pdf_name, pos.pdf_path
    session.add(row)
    _save(session, actor, row, "add", None, pos.id, f"внесена из позиции {pos.id} (заказ {pos.order.number})")
    return entry


def _store_files(row: JournalRow, files: list[UploadedFile], storage_dir: Path | str) -> list[str]:
    """Сохраняет файлы детали; нечитаемые отклоняются, файлы другой детали — с предупреждением."""
    folder = Path(storage_dir) / f"entry_{row.id}"
    folder.mkdir(parents=True, exist_ok=True)
    checked = []
    for f in files:
        try:
            kind = file_kind(f.filename)
        except ValueError as err:
            raise ServiceError(str(err)) from None
        name = Path(f.filename.replace("\\", "/")).name
        tmp = folder / f"_check_{name}"
        tmp.write_bytes(f.content)
        reason = unreadable_reason(kind, str(tmp))
        tmp.unlink(missing_ok=True)
        if reason:
            raise ServiceError(f"Файл «{name}» отклонён: {reason}")
        checked.append((kind, name, f.content))
    for kind, name, content in checked:
        path = folder / name
        path.write_bytes(content)
        setattr(row, f"{kind}_name", name)
        setattr(row, f"{kind}_path", str(path))
    return [s.warning for s in check_entry_files(row) if s.warning]


def create_entry_from_files(session: Session, actor: Actor, files: list[UploadedFile],
                            storage_dir: Path | str) -> JournalRow:
    """Новая деталь журнала по STEP и PDF: данные читаются из файлов, время предлагается по аналогу."""
    require(actor.role, Action.EDIT_JOURNAL)
    pairing = pair_files([f.filename for f in files])
    if len(pairing.pairs) != 1 or pairing.unpaired:
        raise ServiceError("Загрузите один STEP и один PDF одной детали с одинаковым именем")
    code = journal_code(next(iter(pairing.pairs)))
    _ensure_new(session, code)

    tmp = Path(storage_dir) / "_incoming"
    tmp.mkdir(parents=True, exist_ok=True)
    paths = {}
    for f in files:
        path = tmp / Path(f.filename).name
        path.write_bytes(f.content)
        paths[file_kind(f.filename)] = str(path)
    data = read_documents(paths["step"], paths["pdf"], [e.material for e in journal_entries(session)])["data"]
    shutil.rmtree(tmp, ignore_errors=True)
    if not data.get("dims"):
        raise ServiceError("Габариты не прочитаны из STEP: создайте деталь импортом Excel или исправьте модель")

    grade = data.get("tolerance_grade") or DEFAULT_GRADE
    features = PartFeatures(Dimensions.from_list(data["dims"]), data.get("material") or "", grade,
                            estimate_complexity(grade, len(data.get("threads") or [])), "milling")
    analog = pick_analog(features, journal_entries(session))
    machine = preferred_machines(session).get(features.technology, "")
    entry = CalibrationEntry(
        part_code=code, name=designation(code), machine_code=machine, material=features.material,
        dims=features.dims, tolerance_grade=grade, complexity=features.complexity,
        stages=analog.route if analog else list(MANUAL_ROUTE), technology=features.technology,
    )
    row = entry_to_row(entry)
    session.add(row)
    session.flush()
    _store_files(row, files, storage_dir)
    note = f"время предложено по аналогу {analog.entry.part_code}" if analog else "время не задано"
    _save(session, actor, row, "add", None, code, f"создана по STEP+PDF; {note}")
    return row


_FIELD_TYPES = {"part_code": str, "name": str, "machine_code": str, "material": str,
                "tolerance_grade": int, "complexity": int, "technology": str}


def update_entry(session: Session, actor: Actor, entry_id: int, fields: dict, reason: str = "") -> JournalRow:
    require(actor.role, Action.EDIT_JOURNAL)
    row = entry_row(session, entry_id)
    changes = {}
    for key, value in fields.items():
        if key == "dims":
            try:
                value = Dimensions(*(float(v) for v in value)).as_list()
            except (TypeError, ValueError):
                raise ServiceError("Габариты: три положительных размера, мм") from None
        elif key in _FIELD_TYPES:
            try:
                value = _FIELD_TYPES[key](value).strip() if _FIELD_TYPES[key] is str else _FIELD_TYPES[key](value)
            except (TypeError, ValueError):
                raise ServiceError(f"Некорректное значение поля «{key}»") from None
        else:
            raise ServiceError(f"Недопустимое поле «{key}»")
        if getattr(row, key) != value:
            changes[key] = value
    if "machine_code" in changes:
        get_machine(session, changes["machine_code"])
    if "technology" in changes and changes["technology"] not in TECHNOLOGIES:
        raise ServiceError("Технология: milling или turning")
    if "tolerance_grade" in changes and not 1 <= changes["tolerance_grade"] <= 18:
        raise ServiceError("Квалитет должен быть от 1 до 18")
    if "part_code" in changes:
        if not changes["part_code"]:
            raise ServiceError("Обозначение не может быть пустым")
        _ensure_new(session, changes["part_code"])
    if not changes:
        return row
    for key, value in changes.items():
        record(session, actor, "journal", row.part_code, key, getattr(row, key), value, reason)
        setattr(row, key, value)
    _save(session, actor, row, "update", None, sorted(changes), reason)
    return row


def set_stage_times(session: Session, actor: Actor, entry_id: int,
                    stages: dict[Stage, tuple[float | None, CountMode]], reason: str = "") -> JournalRow:
    """Время каждой операции (на единицу счёта); пустое время — операции нет в маршруте."""
    require(actor.role, Action.EDIT_JOURNAL)
    row = entry_row(session, entry_id)
    current = {s.stage: s for s in route_from_list(row.stages)}
    for stage, (hours, mode) in stages.items():
        if hours is None:
            current.pop(stage, None)
            continue
        try:
            current[stage] = StageNorm(Stage(stage), float(hours), CountMode(mode))
        except ValueError as err:
            raise ServiceError(f"{Stage(stage).label}: {err}") from None
    old = row.stages
    row.stages = [s.to_dict() for s in route_from_list([s.to_dict() for s in current.values()])]
    _save(session, actor, row, "stages", old, row.stages, reason)
    return row


def add_manual_measurement(session: Session, actor: Actor, entry_id: int, group: StageGroup,
                           calc_hours: float, actual_hours: float, reason: str = "") -> JournalRow:
    """Замер, внесённый технологом вручную (например, по прошлым партиям): обучает k."""
    require(actor.role, Action.EDIT_JOURNAL)
    row = entry_row(session, entry_id)
    try:
        entry = add_measurement(entry_from_row(row), StageGroup(group), float(calc_hours), float(actual_hours))
    except ValueError as err:
        raise ServiceError(str(err)) from None
    entry_to_row(entry, row)
    _save(session, actor, row, "measurement", None,
          {"group": StageGroup(group).value, "calc_hours": calc_hours, "actual_hours": actual_hours},
          reason or "замер внесён вручную")
    return row


def attach_files(session: Session, actor: Actor, entry_id: int, files: list[UploadedFile],
                 storage_dir: Path | str) -> list[str]:
    """Прикрепить или заменить STEP и PDF; возвращает предупреждения о несовпадении обозначения."""
    require(actor.role, Action.EDIT_JOURNAL)
    row = entry_row(session, entry_id)
    if not files:
        raise ServiceError("Выберите файлы STEP и/или PDF")
    warnings = _store_files(row, files, storage_dir)
    _save(session, actor, row, "files", None, {"step": row.step_name, "pdf": row.pdf_name})
    return warnings


def entries_without_files(session: Session) -> int:
    """Сколько деталей журнала без исправных STEP и PDF."""
    return sum(1 for row in journal_rows(session) if not all(s.ok for s in check_entry_files(row)))


def delete_entry(session: Session, actor: Actor, entry_id: int, reason: str = "") -> None:
    require(actor.role, Action.EDIT_JOURNAL)
    row = entry_row(session, entry_id)
    code = row.part_code
    session.delete(row)
    bump_journal_version(session)
    record(session, actor, "journal", code, "delete", code, None, reason)
    session.commit()
