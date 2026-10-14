"""Приём заказа и чтение документации (ФТ-01…ФТ-04)."""
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import PositionStatus
from app.domain.order_intake import designation, file_kind, pair_files, validate_position_count
from app.domain.status import is_editable
from app.parsers.pdf_reader import read_pdf
from app.parsers.step_reader import read_step
from app.services.actor import Actor, ServiceError
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
        read["step"] = {"dims": step.dims.as_list() if step.dims else None, "volume": step.volume,
                        "bodies": step.bodies, "errors": step.errors}
        data["dims"], data["volume"] = read["step"]["dims"], step.volume
    if pdf_path:
        pdf = read_pdf(pdf_path, known_materials)
        read["pdf"] = {"material": pdf.material, "tolerance_grade": pdf.tolerance_grade,
                       "fits": pdf.fits, "tolerances": pdf.tolerances, "threads": pdf.threads,
                       "errors": pdf.errors}
        data.update(material=pdf.material, tolerance_grade=pdf.tolerance_grade,
                    threads=pdf.threads, fits=pdf.fits)
    return {"read": read, "data": data}


def _apply_reading(position: PositionRow, known_materials: list[str]) -> None:
    result = read_documents(position.step_path, position.pdf_path, known_materials)
    position.read_data = result["read"]
    position.data = result["data"]


def create_order(session: Session, actor: Actor, start_date: date | None, files: list[UploadedFile],
                 upload_dir: Path | str, number: str | None = None) -> OrderRow:
    require(actor.role, Action.UPLOAD_ORDER)
    if start_date is None:
        raise ServiceError("Укажите дату начала изготовления")
    names = [_safe_name(f.filename) for f in files]
    for name in names:
        file_kind(name)
    pairing = pair_files(names)
    validate_position_count(pairing.position_count)

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
        pos = PositionRow(designation=desig, quantity=1, status=PositionStatus.CALCULATION.value,
                          step_name=pair.get("step"), step_path=paths.get(pair.get("step")),
                          pdf_name=pair.get("pdf"), pdf_path=paths.get(pair.get("pdf")))
        _apply_reading(pos, known)
        order.positions.append(pos)
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
    if quantity < 1:
        raise ServiceError("Количество должно быть не меньше 1")
    if pos.quantity != quantity:
        record(session, actor, "position", pos.id, "quantity", pos.quantity, quantity)
        pos.quantity = quantity
        pos.current_calc = None
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
    session.commit()
    session.refresh(pos.order)
    return pos
