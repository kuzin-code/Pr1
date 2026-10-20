"""История коммерческих предложений: каждое КП сохраняется (снимок данных и файл Word) — по заказу или по
быстрому расчёту. Поиск по заказчику, номеру и обозначению детали; аналогичные заказы — КП с теми же деталями."""
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.desired_date import ProposalRange, RangeCheck
from app.services import quick_service
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.proposal_docx import write_docx
from app.services.proposal_service import VALID_DAYS, Proposal, ProposalItem, build_proposal
from app.storage.orm import ProposalRow

_DATES = ("date", "valid_until", "start", "desired")


def to_dict(p: Proposal) -> dict:
    data = asdict(p)
    for key in _DATES:
        data[key] = data[key].isoformat() if data[key] else None
    data["calc_date"] = p.calc_date.isoformat() if p.calc_date else None
    if p.range:
        data["range"] = {**data["range"], **{k: getattr(p.range, k).isoformat()
                                           for k in ("finish", "earliest", "latest")}}
    return data


def from_dict(data: dict) -> Proposal:
    d = dict(data)
    for key in _DATES:
        d[key] = date.fromisoformat(d[key]) if d.get(key) else None
    d["calc_date"] = datetime.fromisoformat(d["calc_date"]) if d.get("calc_date") else None
    r = d.get("range")
    d["range"] = ProposalRange(date.fromisoformat(r["finish"]), r["duration"], r["delta"],
                               date.fromisoformat(r["earliest"]), date.fromisoformat(r["latest"])) if r else None
    d["desired_check"] = RangeCheck(**d["desired_check"])
    d["items"] = [ProposalItem(**i) for i in d["items"]]
    return Proposal(**d)


def proposal_of(row: ProposalRow) -> Proposal:
    return from_dict(row.data)


def _save(session: Session, actor: Actor, p: Proposal, storage_dir: Path | str, source: str,
          order_id: int | None) -> ProposalRow:
    row = ProposalRow(number=p.number, created_at=datetime.now(), created_by=actor.username, source=source,
                      order_id=order_id, order_number=p.order_number or None, customer=p.customer or "",
                      designations=" | ".join(f"{i.designation} {i.name}".strip() for i in p.items),
                      grand_total=round(p.grand_total, 2), data={})
    session.add(row)
    session.flush()
    if order_id is None:
        p.number = row.number = f"КП-Б-{row.id:04d}"
    else:
        same = session.scalar(select(ProposalRow.id).where(ProposalRow.order_id == order_id,
                                                            ProposalRow.id != row.id).limit(1))
        if same is not None:
            count = len(list(session.scalars(select(ProposalRow.id).where(ProposalRow.order_id == order_id))))
            p.number = row.number = f"{p.number}/{count}"
    row.data = to_dict(p)
    folder = Path(storage_dir)
    folder.mkdir(parents=True, exist_ok=True)
    row.file_path = str(write_docx(p, folder / f"proposal_{row.id}.docx"))
    record(session, actor, "proposal", row.id, "create", None,
           {"number": row.number, "total": row.grand_total, "order": row.order_number}, "сформировано КП")
    session.commit()
    return row


def create_from_order(session: Session, actor: Actor, order_id: int, storage_dir: Path | str) -> ProposalRow:
    p = build_proposal(session, actor, order_id)
    return _save(session, actor, p, storage_dir, "order", order_id)


def create_from_quick(session: Session, actor: Actor, work_dir: Path | str, token: str,
                      storage_dir: Path | str) -> ProposalRow:
    """КП по быстрому расчёту — без заказа; данные — как посчитано (с правками пользователя)."""
    require(actor.role, Action.UPLOAD_ORDER)
    s = quick_service.load(work_dir, token)
    today = date.today()
    r = s.get("range")
    span = ProposalRange(date.fromisoformat(s["calc_date"][:10]), r["duration"], r["delta"],
                         date.fromisoformat(r["earliest"]), date.fromisoformat(r["latest"])) if r else None
    desired = date.fromisoformat(s["desired"]) if s.get("desired") else None
    items = [ProposalItem(i["designation"], i["name"], i["material"], i["quantity"], i["machine_hours"],
                          i["bench_hours"], i["machine_rate"], i["bench_rate"], i["batch_price"]) for i in s["items"]]
    p = Proposal(number="КП-Б", date=today, valid_until=today + timedelta(days=VALID_DAYS), order_number="",
                 customer=s.get("customer") or "", contacts={}, items=items,
                 start=date.fromisoformat(s["start"]),
                 calc_date=datetime.fromisoformat(s["calc_date"]) if s.get("calc_date") else None,
                 range=span, desired=desired, desired_check=span.against(desired) if span else RangeCheck("none"),
                 notes=list(s.get("notes") or []))
    return _save(session, actor, p, storage_dir, "quick", None)


def list_proposals(session: Session, actor: Actor, query: str = "", limit: int = 200) -> list[ProposalRow]:
    require(actor.role, Action.UPLOAD_ORDER)
    stmt = select(ProposalRow).order_by(ProposalRow.id.desc()).limit(limit)
    rows = list(session.scalars(stmt))
    q = (query or "").strip().casefold()
    if not q:
        return rows
    return [r for r in rows if q in " ".join((r.number, r.customer or "", r.order_number or "",
                                              r.designations or "")).casefold()]


def get_proposal(session: Session, actor: Actor, proposal_id: int) -> ProposalRow:
    require(actor.role, Action.UPLOAD_ORDER)
    row = session.get(ProposalRow, proposal_id)
    if row is None:
        raise ServiceError("Коммерческое предложение не найдено")
    return row


def similar(session: Session, designations: list[str], exclude_order: int | None = None,
            limit: int = 10) -> list[ProposalRow]:
    """Прежние КП, в которых были те же детали (по обозначению) — аналогичный заказ."""
    codes = {d.split()[0].casefold() for d in designations if d.strip()}
    if not codes:
        return []
    stmt = select(ProposalRow).order_by(ProposalRow.id.desc())
    found = []
    for row in session.scalars(stmt):
        if exclude_order is not None and row.order_id == exclude_order:
            continue
        theirs = {part.split()[0].casefold() for part in (row.designations or "").split(" | ") if part.strip()}
        if codes & theirs:
            found.append(row)
        if len(found) >= limit:
            break
    return found


def order_proposals(session: Session, order_id: int) -> list[ProposalRow]:
    return list(session.scalars(select(ProposalRow).where(ProposalRow.order_id == order_id)
                                .order_by(ProposalRow.id.desc())))
