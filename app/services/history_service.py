"""Запись и чтение истории расчётов и корректировок (ФТ-05, ФТ-24, Д-8)."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.actor import Actor
from app.storage.orm import HistoryRow


def record(session: Session, actor: Actor, entity: str, entity_id, field: str, old, new,
           reason: str = "", ts: datetime | None = None) -> HistoryRow:
    row = HistoryRow(ts=ts or datetime.now(), username=actor.username, entity=entity,
                     entity_id=str(entity_id), field=field, old=old, new=new, reason=reason)
    session.add(row)
    return row


def list_history(session: Session, entity: str | None = None, entity_id=None,
                 limit: int = 500) -> list[HistoryRow]:
    query = select(HistoryRow).order_by(HistoryRow.id.desc()).limit(limit)
    if entity:
        query = query.where(HistoryRow.entity == entity)
    if entity_id is not None:
        query = query.where(HistoryRow.entity_id == str(entity_id))
    return list(session.scalars(query))
