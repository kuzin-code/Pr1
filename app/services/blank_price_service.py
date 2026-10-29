"""Цена заготовок по прайсу поставщика (domain/blank_price.py): загрузка прайса, таблица марок, цена позиции.

Прайс price.xls скачивает с сайта prutki.ru в браузере IT или технолог и загружает в «Справочниках»
(приложение в интернет не ходит, НФТ-01). Новый прайс заменяет старый целиком — в историю с причиной.
Позиции прайса — таблица blank_prices, дата и ставка НДС — параметр «blank_price_list».
Цену куска технолог может задать вручную (data["blank_price"], с причиной) — «задано технологом».
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.blank_price import (
    FORMS,
    BlankCost,
    PriceItem,
    PriceList,
    PriceRules,
    RulesError,
    blank_cost,
    parse_grades,
)
from app.domain.enums import PositionStatus
from app.domain.history import require_reason
from app.parsers.price_list import PriceFormatError, parse_price_list
from app.services.actor import Actor, ServiceError
from app.services.blank_service import position_blank
from app.services.history_service import record
from app.services.order_service import get_position
from app.storage.orm import BlankPriceRow, Parameter, PositionRow

META, RULES = "blank_price_list", "blank_price_rules"
MANUAL_KEY = "blank_price"  # цена куска, заданная технологом, в data позиции
MAX_FILE_MB = 20


def price_meta(session: Session) -> dict | None:
    """Дата прайса, ставка НДС, файл, кто и когда загрузил, число позиций; прайс не загружен — None."""
    row = session.get(Parameter, META)
    return dict(row.value) if row and row.value else None


def rules(session: Session) -> PriceRules:
    row = session.get(Parameter, RULES)
    try:
        return PriceRules.from_dict(row.value if row else None)
    except RulesError:
        return PriceRules.from_dict(None)


def load_price(session: Session, actor: Actor, content: bytes, filename: str, reason: str) -> dict:
    """Загрузить прайс: старый заменяется целиком; в историю — дата и число позиций было / стало, причина."""
    require(actor.role, Action.EDIT_REFERENCE)
    reason = require_reason(reason)
    if len(content or b"") > MAX_FILE_MB * 1024 * 1024:
        raise ServiceError(f"Файл прайса больше {MAX_FILE_MB} МБ: это не прайс заготовок")
    try:
        price, skipped = parse_price_list(content, filename)
    except PriceFormatError as err:
        raise ServiceError(f"Прайс не загружен: {err}") from None
    old = price_meta(session)
    session.execute(delete(BlankPriceRow))
    session.add_all(BlankPriceRow(code=i.code, name=i.name, form=i.form, grade=i.grade, dims=list(i.dims),
                                  price=i.price, stock=i.stock) for i in price.items)
    meta = {"date": price.date, "vat_percent": price.vat_percent, "file": filename, "count": len(price.items),
            "skipped": skipped, "loaded_at": datetime.now().isoformat(timespec="seconds"), "by": actor.username}
    row = session.get(Parameter, META)
    if row is None:
        session.add(Parameter(key=META, value=meta))
    else:
        row.value = meta
    short = ("date", "count", "file")
    record(session, actor, "parameters", META, META, {k: old.get(k) for k in short} if old else None,
           {k: meta[k] for k in short}, reason)
    session.commit()
    return meta


def price_list(session: Session, kind: str | None = None) -> PriceList | None:
    """Прайс из базы (только формы сортамента kind, чтобы не читать все 19 тысяч строк); не загружен — None."""
    meta = price_meta(session)
    if meta is None:
        return None
    query = select(BlankPriceRow)
    if kind is not None:
        query = query.where(BlankPriceRow.form.in_(FORMS.get(kind, ())))
    items = tuple(PriceItem(r.code, r.name, r.form, r.grade, tuple(r.dims or ()), r.price, r.stock)
                  for r in session.scalars(query))
    return PriceList(meta.get("date"), meta.get("vat_percent"), items)


def position_cost(session: Session, pos: PositionRow, current: PriceRules | None = None) -> BlankCost:
    """Стоимость заготовок позиции: по заготовке технолога или предложению системы; цена куска технолога важнее."""
    blank = position_blank(session, pos)
    manual = (pos.data or {}).get(MANUAL_KEY)
    if manual:  # позиции прайса не нужны — только ставка НДС загруженного прайса
        meta = price_meta(session)
        price = PriceList(meta.get("date"), meta.get("vat_percent"), ()) if meta else None
    else:
        price = price_list(session, blank.kind)
    return blank_cost(blank, price, current or rules(session), manual)


def _editable(session: Session, actor: Actor, position_id: int) -> PositionRow:
    require(actor.role, Action.EDIT_TECH_DATA)
    pos = get_position(session, position_id)
    if pos.status == PositionStatus.DONE.value:
        raise ServiceError("Позиция выполнена: цена заготовки не меняется")
    return pos


def _label(cost: BlankCost) -> str:
    if cost.unit_price is None:
        return f"не определена: {cost.reason}"
    if cost.status == "manual":
        return f"{cost.unit_price:g} ₽/шт с НДС (задано технологом)"
    return f"{cost.unit_price:g} ₽/шт с НДС × {cost.pieces} (прайс, код {cost.piece.code}, {cost.cut_text})"


def set_manual_price(session: Session, actor: Actor, position_id: int, raw, note: str, reason: str) -> BlankCost:
    """Технолог задаёт цену куска (с НДС, за шт.) — формула та же: цена × (заготовок + запас на брак)."""
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    try:
        price = float(str(raw).replace(",", ".").replace(" ", "").strip())
    except ValueError:
        raise ServiceError("Цена куска — число, ₽ за штуку с НДС") from None
    if not 0 < price < 10_000_000:
        raise ServiceError("Цена куска должна быть больше нуля")
    old = position_cost(session, pos)
    pos.data = {**(pos.data or {}), MANUAL_KEY: {"price": price, "note": (note or "").strip(), "by": actor.username}}
    new = position_cost(session, pos)
    record(session, actor, "position", pos.id, MANUAL_KEY, _label(old), _label(new), reason)
    session.commit()
    return new


def reset_manual_price(session: Session, actor: Actor, position_id: int, reason: str) -> BlankCost:
    """Вернуть цену по прайсу (цена технолога снимается, с причиной)."""
    pos = _editable(session, actor, position_id)
    reason = require_reason(reason)
    old = position_cost(session, pos)
    pos.data = {k: v for k, v in (pos.data or {}).items() if k != MANUAL_KEY}
    new = position_cost(session, pos)
    record(session, actor, "position", pos.id, MANUAL_KEY, _label(old), _label(new), reason)
    session.commit()
    return new


def set_rules(session: Session, actor: Actor, grades: str, scrap, vat, reason: str, kerf="0") -> PriceRules:
    """Таблица марок, запас на брак и НДС прайса по умолчанию — кто ведёт справочник; в историю с причиной."""
    require(actor.role, Action.EDIT_REFERENCE)
    reason = require_reason(reason)
    try:
        parsed = parse_grades(grades)
        new = PriceRules.from_dict({"scrap_pieces": str(scrap).strip(), "price_vat_percent":
                                    str(vat).replace(",", ".").strip(), "kerf_mm": str(kerf).replace(",", ".").strip()
                                    or "0", "grades": [g.to_dict() for g in parsed]})
    except RulesError as err:
        raise ServiceError(str(err)) from None
    if not new.grades:
        raise ServiceError("Соответствие марок: заполните хотя бы одну строку")
    row = session.get(Parameter, RULES)
    old = rules(session).to_dict()
    if row is None:
        session.add(Parameter(key=RULES, value=new.to_dict()))
    else:
        row.value = new.to_dict()
    record(session, actor, "parameters", RULES, RULES, old, new.to_dict(), reason)
    session.commit()
    return new
