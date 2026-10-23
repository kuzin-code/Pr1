"""Приём заказа и чтение документации (ФТ-01…ФТ-04, ФТ-32)."""
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, can, require
from app.domain.enums import PositionStatus
from app.domain.order_intake import (designation, file_kind, match_files, pair_files, validate_position_count,
                                     validate_quantity)
from app.domain.status import is_editable
from app.parsers.order_list import OrderListItem, cooperation_hint, read_order_list
from app.parsers.pdf_reader import read_pdf
from app.parsers.step_features import read_features
from app.parsers.step_reader import read_step
from app.services.actor import Actor, ServiceError
from app.services.auto_service import auto_prepare
from app.services.file_check import unreadable_reason
from app.services.history_service import record
from app.services.reference_service import journal_entries
from app.storage.orm import OrderRow, PositionRow


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes


def _safe_name(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name.strip()
    if not name or name in (".", ".."):
        raise ServiceError("Некорректное имя файла")
    return name


def read_documents(step_path: str | None, pdf_path: str | None, known_materials: list[str]) -> dict:
    """Считанные данные и итоговые значения позиции (ФТ-03, ФТ-04)."""
    read: dict = {}
    data: dict = {"dims": None, "volume": None, "material": None, "tolerance_grade": None,
                  "threads": [], "fits": [], "technology": "milling"}
    if step_path:
        step = read_step(step_path)
        found = read_features(step_path)  # отверстия и внутренние радиусы — для проверки технологичности
        read["step"] = {"dims": step.dims.as_list() if step.dims else None, "volume": step.volume,
                        "bodies": step.bodies, "errors": step.errors,
                        "features": found.to_dict() if found else None}
        data["dims"], data["volume"] = read["step"]["dims"], step.volume
    if pdf_path:
        pdf = read_pdf(pdf_path, known_materials)
        read["pdf"] = {"material": pdf.material, "tolerance_grade": pdf.tolerance_grade,
                       "fits": pdf.fits, "tolerances": pdf.tolerances, "threads": pdf.threads,
                       "errors": pdf.errors}
        data.update(material=pdf.material, tolerance_grade=pdf.tolerance_grade,
                    threads=pdf.threads, fits=pdf.fits)
        if pdf.eskd is not None:
            e = pdf.eskd
            data["drawing"] = {
                "designation": e.designation, "name": e.name, "mass_kg": e.mass_kg, "scale": e.scale,
                "sheet_format": e.sheet_format, "sortament": e.sortament, "blank": e.blank,
                "general_grade": e.general_grade, "ra_min": e.ra_min, "thread_count": e.thread_count,
                "hole_count": e.hole_count, "has_form_tolerances": e.has_form_tolerances,
                "coating": e.coating, "heat_treatment": e.heat_treatment, "requirements": e.requirements,
                "external_operations": e.external_operations}
    return {"read": read, "data": data}


def _apply_reading(position: PositionRow, known_materials: list[str]) -> None:
    result = read_documents(position.step_path, position.pdf_path, known_materials)
    position.read_data = result["read"]
    position.data = result["data"]


def create_order(session: Session, actor: Actor, start_date: date | None, files: list[UploadedFile],
                 upload_dir: Path | str, number: str | None = None, quantity: int = 1) -> OrderRow:
    require(actor.role, Action.UPLOAD_ORDER)
    if start_date is None:
        raise ServiceError("Укажите дату начала изготовления")
    names = [_safe_name(f.filename) for f in files]
    for name in names:
        file_kind(name)
    pairing = pair_files(names)
    validate_position_count(pairing.position_count)
    validate_quantity(quantity)

    order = OrderRow(number=number or "", start_date=start_date, created_at=datetime.now(),
                     created_by=actor.username)
    session.add(order)
    session.flush()
    order.number = order.number or f"З-{order.id:04d}"
    folder = Path(upload_dir) / f"order_{order.id}"
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for f, name in zip(files, names):
        path = folder / name
        path.write_bytes(f.content)
        paths[name] = str(path)

    known = [e.material for e in journal_entries(session)]
    groups = [(d, pair) for d, pair in pairing.pairs.items()]
    groups += [(u.designation, {u.kind: u.filename}) for u in pairing.unpaired]
    for desig, pair in groups:
        pos = PositionRow(designation=desig, quantity=quantity, status=PositionStatus.CALCULATION.value,
                          step_name=pair.get("step"), step_path=paths.get(pair.get("step")),
                          pdf_name=pair.get("pdf"), pdf_path=paths.get(pair.get("pdf")))
        _apply_reading(pos, known)
        order.positions.append(pos)
    session.flush()
    # Расчёт формируется сам; технолог проверяет и при необходимости корректирует.
    for pos in order.positions:
        auto_prepare(session, pos)
    session.commit()
    return order


def list_orders(session: Session) -> list[OrderRow]:
    return list(session.scalars(select(OrderRow).order_by(OrderRow.id.desc())))


def get_order(session: Session, order_id: int) -> OrderRow:
    order = session.get(OrderRow, order_id)
    if order is None:
        raise ServiceError("Заказ не найден")
    return order


def get_position(session: Session, position_id: int) -> PositionRow:
    pos = session.get(PositionRow, position_id)
    if pos is None:
        raise ServiceError("Позиция не найдена")
    return pos


def ensure_editable(pos: PositionRow) -> None:
    if not is_editable(PositionStatus(pos.status)):
        raise ServiceError("Позиция утверждена или передана на утверждение: правка вручную недоступна")


def set_quantity(session: Session, actor: Actor, position_id: int, quantity: int) -> PositionRow:
    require(actor.role, Action.UPLOAD_ORDER)
    pos = get_position(session, position_id)
    ensure_editable(pos)
    validate_quantity(quantity)
    if pos.quantity != quantity:
        record(session, actor, "position", pos.id, "quantity", pos.quantity, quantity)
        pos.quantity = quantity
        auto_prepare(session, pos)
    session.commit()
    return pos


def link_files(session: Session, actor: Actor, position_id: int, other_id: int) -> PositionRow:
    """Ручное связывание STEP и PDF одной детали технологом (ФТ-02)."""
    require(actor.role, Action.EDIT_TECH_DATA)
    pos, other = get_position(session, position_id), get_position(session, other_id)
    if pos.order_id != other.order_id or pos.id == other.id:
        raise ServiceError("Связывать можно только разные позиции одного заказа")
    ensure_editable(pos)
    step_src = pos if pos.step_path else other
    pdf_src = pos if pos.pdf_path else other
    if not (step_src.step_path and pdf_src.pdf_path) or (pos.step_path and pos.pdf_path):
        raise ServiceError("Для связывания нужны позиция с одним файлом и позиция с файлом другого типа")
    pos.step_name, pos.step_path = step_src.step_name, step_src.step_path
    pos.pdf_name, pos.pdf_path = pdf_src.pdf_name, pdf_src.pdf_path
    pos.designation = designation(pos.step_name)
    _apply_reading(pos, [e.material for e in journal_entries(session)])
    record(session, actor, "position", pos.id, "files", None,
           {"step": pos.step_name, "pdf": pos.pdf_name}, "ручное связывание STEP и PDF")
    session.delete(other)
    session.flush()
    auto_prepare(session, pos)
    session.commit()
    session.refresh(pos.order)
    return pos


# --- заказ списком деталей (ФТ-32) ----------------------------------------------------------------

def _material_key(material: str | None) -> str:
    """Материал без ГОСТ/ТУ и пробелов — для сравнения списка с чертежом."""
    return re.split(r"\s+(?:ГОСТ|ТУ|GOST)\b", material or "", maxsplit=1)[0].replace(" ", "").casefold()


def _apply_list_item(pos: PositionRow, item: OrderListItem) -> None:
    """Данные строки списка: раздел, покрытие, примечание; материал — если не прочитан из чертежа."""
    data, issues = dict(pos.data or {}), []
    data.update(name=item.name, section=item.section, coating=item.coating, note=item.note,
                list_material=item.material)
    if item.material and not data.get("material"):
        data["material"] = item.material
    elif item.material and _material_key(item.material) != _material_key(data.get("material")):
        issues.append(f"Материал в списке «{item.material}» отличается от чертежа «{data['material']}»: "
                      "принят чертёж, проверьте")
    hint = cooperation_hint(f"{item.coating} {item.note}")
    if hint:
        pos.cooperation = True
        issues.append(f"Внешняя операция по списку («{item.coating or item.note}»): расчёт без кооперации")
    data["list_issues"] = issues
    pos.data = data


def create_order_from_list(session: Session, actor: Actor, start_date: date | None, list_file: UploadedFile,
                           files: list[UploadedFile], upload_dir: Path | str,
                           number: str | None = None) -> tuple[OrderRow, list[str]]:
    """Заказ по списку деталей заказчика: позиция на каждую строку, файлы — по обозначению."""
    require(actor.role, Action.UPLOAD_ORDER)
    if start_date is None:
        raise ServiceError("Укажите дату начала изготовления")
    folder = Path(upload_dir) / "_lists"
    folder.mkdir(parents=True, exist_ok=True)
    list_path = folder / f"{datetime.now():%Y%m%d%H%M%S%f}_{_safe_name(list_file.filename)}"
    list_path.write_bytes(list_file.content)
    parsed = read_order_list(list_path)
    validate_position_count(len(parsed.items))
    names = [_safe_name(f.filename) for f in files]
    matched, unmatched = match_files([i.designation for i in parsed.items], names)
    warnings = [f"Файл «{n}» не относится ни к одной детали списка — не загружен" for n in unmatched]

    order = OrderRow(number=number or "", start_date=start_date, created_at=datetime.now(),
                     created_by=actor.username, customer=parsed.customer or None,
                     customer_order_date=parsed.order_date, contacts=parsed.contacts or None,
                     source_list=_safe_name(list_file.filename), desired_date=parsed.desired_date)
    session.add(order)
    session.flush()
    order.number = order.number or f"З-{order.id:04d}"
    order_dir = Path(upload_dir) / f"order_{order.id}"
    order_dir.mkdir(parents=True, exist_ok=True)
    list_path.replace(order_dir / _safe_name(list_file.filename))
    content = dict(zip(names, (f.content for f in files)))
    known = [e.material for e in journal_entries(session)]
    for item in parsed.items:
        pos = PositionRow(designation=f"{item.designation} {item.name}".strip(), quantity=item.quantity,
                          status=PositionStatus.CALCULATION.value)
        for kind, name in matched.get(item.designation, {}).items():
            path = order_dir / name
            path.write_bytes(content[name])
            setattr(pos, f"{kind}_name", name)
            setattr(pos, f"{kind}_path", str(path))
        _apply_reading(pos, known)
        _apply_list_item(pos, item)
        order.positions.append(pos)
    session.flush()
    for pos in order.positions:
        auto_prepare(session, pos)
    record(session, actor, "order", order.id, "list", None,
           {"file": order.source_list, "positions": len(parsed.items)}, "заказ по списку деталей")
    session.commit()
    return order, warnings


def attach_position_files(session: Session, actor: Actor, position_id: int, files: list[UploadedFile],
                          upload_dir: Path | str) -> PositionRow:
    """Прикрепить или заменить STEP/PDF позиции (мастер, технолог); позиция пересчитывается."""
    if not (can(actor.role, Action.UPLOAD_ORDER) or can(actor.role, Action.EDIT_TECH_DATA)):
        require(actor.role, Action.UPLOAD_ORDER)
    pos = get_position(session, position_id)
    ensure_editable(pos)
    if not files:
        raise ServiceError("Выберите файлы STEP и/или PDF")
    folder = Path(upload_dir) / f"order_{pos.order_id}"
    folder.mkdir(parents=True, exist_ok=True)
    for f in files:
        name = _safe_name(f.filename)
        kind = file_kind(name)
        path = folder / name
        path.write_bytes(f.content)
        reason = unreadable_reason(kind, str(path))
        if reason:
            path.unlink(missing_ok=True)
            raise ServiceError(f"Файл «{name}» отклонён: {reason}")
        setattr(pos, f"{kind}_name", name)
        setattr(pos, f"{kind}_path", str(path))
    current = dict(pos.data or {})
    _apply_reading(pos, [e.material for e in journal_entries(session)])
    keep = set(current.get("edited") or []) | {"name", "section", "coating", "note", "list_material", "auto",
                                                "edited", "list_issues"}
    pos.data = {**pos.data, **{k: v for k, v in current.items() if k in keep}}
    if current.get("list_material") and not pos.data.get("material"):
        pos.data = {**pos.data, "material": current["list_material"]}
    record(session, actor, "position", pos.id, "files", None, {"step": pos.step_name, "pdf": pos.pdf_name},
           "файлы прикреплены")
    auto_prepare(session, pos)
    session.commit()
    return pos


# --- удаление расчёта до утверждения ----------------------------------------------------------------

DELETABLE = {PositionStatus.CALCULATION.value, PositionStatus.TECH_REVIEW.value, PositionStatus.AWAITING_APPROVAL.value}


def _can_delete(actor: Actor) -> None:
    if not (can(actor.role, Action.UPLOAD_ORDER) or can(actor.role, Action.EDIT_TECH_DATA)):
        require(actor.role, Action.UPLOAD_ORDER)


def _check_deletable(pos: PositionRow) -> None:
    if pos.status not in DELETABLE:
        raise ServiceError(f"Позиция «{pos.designation}» утверждена или в работе — удалить нельзя, план меняется "
                           "только по факту")


def _drop_position(session: Session, pos: PositionRow, keep_files: set[str]) -> None:
    from app.storage.orm import NotificationRow

    for row in session.scalars(select(NotificationRow).where(NotificationRow.position_id == pos.id)):
        session.delete(row)
    for path in (pos.step_path, pos.pdf_path):
        if path and path not in keep_files:
            Path(path).unlink(missing_ok=True)
    session.delete(pos)


def delete_position(session: Session, actor: Actor, position_id: int, reason: str) -> int | None:
    """Удалить позицию (расчёт) до утверждения: с причиной, в историю. Последняя позиция — удаляется и заказ.
    Возвращает номер заказа, если заказ остался."""
    from app.domain.history import require_reason

    _can_delete(actor)
    reason = require_reason(reason)
    pos = get_position(session, position_id)
    _check_deletable(pos)
    order = pos.order
    record(session, actor, "position", pos.id, "delete", {"designation": pos.designation, "order": order.number,
                                                          "quantity": pos.quantity}, None, reason)
    others = [p for p in order.positions if p.id != pos.id]
    keep = {f for p in others for f in (p.step_path, p.pdf_path) if f}
    order.positions.remove(pos)
    _drop_position(session, pos, keep)
    if not others:
        _drop_order(session, actor, order, reason)
        session.commit()
        return None
    session.commit()
    return order.id


def _drop_order(session: Session, actor: Actor, order: OrderRow, reason: str) -> None:
    import shutil

    from app.storage.orm import NotificationRow

    record(session, actor, "order", order.id, "delete", {"number": order.number, "customer": order.customer}, None,
           reason)
    for row in session.scalars(select(NotificationRow).where(NotificationRow.key.like(f"new_order:{order.id}:%"))):
        session.delete(row)
    folder = None
    for pos in list(order.positions):
        folder = folder or (Path(pos.step_path or pos.pdf_path).parent if (pos.step_path or pos.pdf_path) else None)
        _drop_position(session, pos, set())
    session.delete(order)
    if folder and folder.name == f"order_{order.id}":
        shutil.rmtree(folder, ignore_errors=True)


def delete_order(session: Session, actor: Actor, order_id: int, reason: str) -> None:
    """Удалить заказ целиком до утверждения: все позиции должны быть не утверждены."""
    from app.domain.history import require_reason

    _can_delete(actor)
    reason = require_reason(reason)
    order = get_order(session, order_id)
    for pos in order.positions:
        _check_deletable(pos)
    _drop_order(session, actor, order, reason)
    session.commit()
