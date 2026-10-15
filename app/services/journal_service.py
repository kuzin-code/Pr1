"""Внесение рассчитанной детали в журнал калибровки: она становится аналогом для следующих расчётов."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.models import CalibrationEntry, Dimensions
from app.domain.route import route_from_list
from app.services.actor import Actor, ServiceError
from app.services.calc_service import position_features
from app.services.history_service import record
from app.services.order_service import get_position
from app.services.reference_service import bump_journal_version
from app.storage.convert import entry_to_row
from app.storage.orm import JournalRow


def journal_code(designation: str) -> str:
    """Обозначение без суффикса копии файла «(1)», который добавляет браузер."""
    return re.sub(r"\s*\(\d+\)$", "", designation).strip()


def add_to_journal(session: Session, actor: Actor, position_id: int) -> CalibrationEntry:
    """Деталь вносится с маршрутом и временем этапов из расчёта; замеры появятся по факту."""
    require(actor.role, Action.EDIT_JOURNAL)
    pos = get_position(session, position_id)
    if not (pos.current_calc or pos.approved_calc):
        raise ServiceError("Позиция ещё не рассчитана")
    if not pos.machine_code:
        raise ServiceError("Не назначен станок")
    code = journal_code(pos.designation)
    if session.scalar(select(JournalRow.id).where(JournalRow.part_code == code)):
        raise ServiceError(f"Деталь «{code}» уже есть в журнале калибровки")
    features = position_features(pos)
    data = pos.data or {}
    entry = CalibrationEntry(
        part_code=code, name=code, machine_code=pos.machine_code,
        material=data.get("material") or "", dims=Dimensions(*features.dims.as_list()),
        tolerance_grade=features.tolerance_grade, complexity=features.complexity,
        stages=route_from_list(pos.route), technology=features.technology,
    )
    session.add(entry_to_row(entry))
    bump_journal_version(session)
    record(session, actor, "journal", entry.part_code, "add", None, pos.id,
           f"внесена из позиции {pos.id} (заказ {pos.order.number})")
    session.commit()
    return entry
