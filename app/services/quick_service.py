"""Быстрый расчёт по файлам: цена, расчётная дата по текущей загрузке ±10 %, занятость станков.

Расчёт выполняется на временной копии базы — рабочая база, план и загрузка не меняются, заказ не создаётся.
Файлы и результат лежат во временной папке до решения: удалить, сообщить начальнику участка или запустить
как заказ (тогда заказ принимается обычным порядком)."""
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.domain.access import Action, require
from app.domain.enums import Role
from app.services import intake_service, plan_service, proposal_service
from app.services.actor import Actor, ServiceError
from app.services.order_service import UploadedFile
from app.storage.db import make_engine
from app.storage.orm import MachineRow

KEEP_HOURS = 24
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


@dataclass
class QuickResult:
    token: str
    summary: dict


def _folder(work_dir: Path | str, token: str) -> Path:
    if not _TOKEN.match(token or ""):
        raise ServiceError("Расчёт не найден")
    return Path(work_dir) / "_quick" / token


def _cleanup(work_dir: Path | str) -> None:
    """Старые расчёты (старше суток) удаляются."""
    root = Path(work_dir) / "_quick"
    limit = datetime.now().timestamp() - KEEP_HOURS * 3600
    for folder in root.glob("*") if root.is_dir() else []:
        if folder.is_dir() and folder.stat().st_mtime < limit:
            shutil.rmtree(folder, ignore_errors=True)


def _copy_database(session: Session, target: Path) -> None:
    session.flush()
    source = session.connection().connection.driver_connection
    with closing(sqlite3.connect(target)) as copy:
        source.backup(copy)


def _summary(copy: Session, actor: Actor, order) -> dict:
    proposal = proposal_service.build_proposal(copy, actor, order.id)
    plan = plan_service.current_plan(copy)
    ids = {p.id for p in order.positions}
    names = {m.code: m.model for m in copy.query(MachineRow)}
    hours = {p.id: (plan_service.plan_calc(p) or {}).get("cost", {}).get("machine_hours", 0) for p in order.positions}
    busy = defaultdict(lambda: {"start": None, "end": None, "hours": 0.0, "positions": set()})
    for slot in plan.slots:
        if slot.position_id not in ids:
            continue
        m = busy[names.get(slot.machine_code, slot.machine_code)]
        m["start"] = min(filter(None, (m["start"], slot.start)))
        m["end"] = max(filter(None, (m["end"], slot.end)))
        if slot.position_id not in m["positions"]:
            m["hours"] += hours.get(slot.position_id, 0)
            m["positions"].add(slot.position_id)
    machines = [{"model": model, "start": v["start"].isoformat(), "end": v["end"].isoformat(),
                 "hours": round(v["hours"], 2), "positions": len(v["positions"])}
                for model, v in sorted(busy.items(), key=lambda kv: kv[1]["start"])]
    bottleneck = max(machines, key=lambda m: m["end"])["model"] if machines else None
    issues = [{"designation": p.designation, "issues": list((p.data or {}).get("auto_issues") or [])}
              for p in order.positions if (p.data or {}).get("auto_issues")]
    r = proposal.range
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "customer": order.customer or "", "start": order.start_date.isoformat(),
        "items": [{"designation": i.designation, "name": i.name, "material": i.material, "quantity": i.quantity,
                   "price": i.price, "batch_price": i.batch_price, "machine_hours": i.machine_hours,
                   "bench_hours": i.bench_hours, "machine_rate": i.machine_rate, "bench_rate": i.bench_rate,
                   "machine_price": i.machine_price, "bench_price": i.bench_price} for i in proposal.items],
        "total": proposal.total, "vat_percent": proposal.vat_percent, "vat": proposal.vat,
        "grand_total": proposal.grand_total,
        "calc_date": proposal.calc_date.isoformat() if proposal.calc_date else None,
        "range": {"earliest": r.earliest.isoformat(), "latest": r.latest.isoformat(), "duration": r.duration,
                  "delta": r.delta} if r else None,
        "desired": order.desired_date.isoformat() if order.desired_date else None,
        "check": {"status": proposal.desired_check.status, "label": proposal.desired_check.label},
        "machines": machines, "bottleneck": bottleneck, "issues": issues,
        "notes": proposal.notes + list(getattr(order, "intake_warnings", [])),
    }


def calculate(session: Session, actor: Actor, files: list[UploadedFile], work_dir: Path | str, quantity: int = 1,
              desired_date: date | None = None, start_date: date | None = None, customer: str = "") -> QuickResult:
    require(actor.role, Action.UPLOAD_ORDER)
    _cleanup(work_dir)
    token = uuid.uuid4().hex
    folder = _folder(work_dir, token)
    (folder / "files").mkdir(parents=True)
    for f in files:
        if f.filename:
            (folder / "files" / Path(f.filename.replace("\\", "/")).name).write_bytes(f.content)
    params = {"quantity": quantity, "desired_date": desired_date.isoformat() if desired_date else None,
              "start_date": (start_date or date.today()).isoformat(), "customer": customer}
    (folder / "params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    tmp = Path(tempfile.mkdtemp(prefix="quick_"))
    engine = None
    try:
        _copy_database(session, tmp / "copy.db")
        engine = make_engine(f"sqlite:///{(tmp / 'copy.db').as_posix()}")
        with sessionmaker(bind=engine)() as copy:
            order = intake_service.quick_intake(copy, actor, _stored_files(folder), tmp / "uploads",
                                                customer=customer, desired_date=desired_date,
                                                start_date=start_date or date.today(), quantity=quantity)
            summary = _summary(copy, actor, order)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)
    summary["params"] = params
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return QuickResult(token, summary)


def _stored_files(folder: Path) -> list[UploadedFile]:
    return [UploadedFile(p.name, p.read_bytes()) for p in sorted((folder / "files").iterdir())]


def load(work_dir: Path | str, token: str) -> dict:
    path = _folder(work_dir, token) / "summary.json"
    if not path.is_file():
        raise ServiceError("Расчёт не найден или уже удалён — посчитайте заново")
    return json.loads(path.read_text(encoding="utf-8"))


def discard(work_dir: Path | str, token: str) -> None:
    shutil.rmtree(_folder(work_dir, token), ignore_errors=True)


def launch(session: Session, actor: Actor, work_dir: Path | str, token: str, upload_dir: Path | str):
    """Запустить как заказ: те же файлы и параметры — обычный приём заказа (с уведомлениями)."""
    summary = load(work_dir, token)
    folder, params = _folder(work_dir, token), summary["params"]
    desired = params.get("desired_date")
    order = intake_service.quick_intake(session, actor, _stored_files(folder), upload_dir,
                                        customer=params.get("customer") or "",
                                        desired_date=date.fromisoformat(desired) if desired else None,
                                        quantity=int(params.get("quantity") or 1))
    discard(work_dir, token)
    return order


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def notify_head(session: Session, actor: Actor, work_dir: Path | str, token: str) -> None:
    """Сводка быстрого расчёта начальнику участка: цена, срок, узкое место, нюансы."""
    require(actor.role, Action.UPLOAD_ORDER)
    s = load(work_dir, token)
    parts = [f"Быстрый расчёт ({Role(actor.role).label}): {len(s['items'])} поз."]
    if s.get("customer"):
        parts[0] += f", {s['customer']}"
    parts.append(f"цена {_money(s['grand_total'])} с НДС")
    if s.get("range"):
        parts.append(f"срок с {date.fromisoformat(s['range']['earliest']):%d.%m.%Y} по "
                     f"{date.fromisoformat(s['range']['latest']):%d.%m.%Y}")
    if s.get("desired"):
        parts.append(f"желаемая {date.fromisoformat(s['desired']):%d.%m.%Y} — {s['check']['label']}")
    if s.get("bottleneck"):
        parts.append(f"узкое место — {s['bottleneck']}")
    nuances = sum(len(i["issues"]) for i in s.get("issues", []))
    if nuances:
        parts.append(f"замечаний системы: {nuances}")
    key = f"quick:{token}:{datetime.now():%Y%m%d%H%M%S%f}"
    plan_service.notify(session, Role.HEAD, key, 0, "quick_calc", datetime.now(), "; ".join(parts) + ".")
    session.commit()
