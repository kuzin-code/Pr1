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
from app.domain.order_intake import validate_quantity
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


def _summary(copy: Session, actor: Actor, order, quantities: list[int] | None = None) -> dict:
    proposal = proposal_service.build_proposal(copy, actor, order.id, quantities=quantities)
    plan = plan_service.current_plan(copy)
    ids = {p.id for p in order.positions}
    names = {m.code: m.model for m in copy.query(MachineRow)}
    hours = {}
    for p in order.positions:
        for t in (plan_service.plan_calc(p) or {}).get("stage_times", []):
            if t["stage"] in ("setup", "machining"):
                key = (p.id, t.get("machine") or p.machine_code)
                hours[key] = hours.get(key, 0.0) + t["planned_hours"]
    busy = defaultdict(lambda: {"start": None, "end": None, "hours": 0.0, "positions": set()})
    for slot in plan.slots:
        if slot.position_id not in ids:
            continue
        m = busy[names.get(slot.machine_code, slot.machine_code)]
        m["start"] = min(filter(None, (m["start"], slot.start)))
        m["end"] = max(filter(None, (m["end"], slot.end)))
        if slot.position_id not in m["positions"]:
            m["hours"] += hours.get((slot.position_id, slot.machine_code), 0)
            m["positions"].add(slot.position_id)
    machines = [{"model": model, "start": v["start"].isoformat(), "end": v["end"].isoformat(),
                 "hours": round(v["hours"], 2), "positions": len(v["positions"])}
                for model, v in sorted(busy.items(), key=lambda kv: kv[1]["start"])]
    bottleneck = max(machines, key=lambda m: m["end"])["model"] if machines else None
    issues = [{"designation": p.designation, "issues": list((p.data or {}).get("auto_issues") or [])}
              for p in order.positions if (p.data or {}).get("auto_issues")]
    r = proposal.range
    positions = []
    for p in order.positions:
        data = p.data or {}
        positions.append({
            "designation": p.designation, "quantity": p.quantity, "material": data.get("material") or "",
            "dims": data.get("dims") or [], "tolerance_grade": data.get("tolerance_grade"),
            "complexity": p.complexity, "technology": data.get("technology") or "milling", "machine": p.machine_code,
            "extra_machines": [dict(e) for e in data.get("extra_machines") or []],
            "route": [dict(s) for s in p.route or []], "analog": p.analog_code,
        })
    _alternatives(copy, order, plan, positions)
    return {
        "positions": positions,
        "cnc": [{"code": m.code, "model": m.model} for m in copy.query(MachineRow) if m.is_cnc],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "customer": order.customer or "", "start": order.start_date.isoformat(),
        "items": [{"designation": i.designation, "name": i.name, "material": i.material, "quantity": i.quantity,
                   "price": i.price, "batch_price": i.batch_price, "machine_hours": i.machine_hours,
                   "bench_hours": i.bench_hours, "machine_rate": i.machine_rate, "bench_rate": i.bench_rate,
                   "machine_price": i.machine_price, "bench_price": i.bench_price, "breaks": i.breaks}
                  for i in proposal.items],
        "quantities": list(quantities or []),
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


def _alternatives(copy: Session, order, plan, positions: list[dict]) -> None:
    """Станки по позиции: время по деталям журнала с этого станка, стоимость, когда свободен и когда будет готово
    (очередь — другие заказы, другие позиции этого расчёта и введённые даты занятости)."""
    from app.domain.enums import StageGroup
    from app.domain.scheduling import work_start
    from app.domain.machines import allowed_machines
    from app.domain.work_calendar import MACHINE_CALENDAR
    from app.domain.analog import same_part
    from app.services.auto_service import machine_route
    from app.services.calc_service import position_features
    from app.services.reference_service import domain_machines, get_rates, journal_entries

    busy: dict[str, datetime] = {m.code: m.busy_until for m in copy.query(MachineRow) if m.busy_until}
    # точка отсчёта — как у плана (начало смены в дату начала заказа, не раньше «сейчас»), иначе «где быстрее»
    # и «Занятость станков» на одной странице расходятся
    base = MACHINE_CALENDAR.normalize(work_start(order.start_date, plan_service.clock()))
    machines = [m for m in domain_machines(copy) if m.is_cnc]
    ids = sorted(p.id for p in order.positions)
    later = {pid: {x for x in ids if x >= pid} for pid in ids}  # сама позиция и стоящие после неё
    journal, rates = journal_entries(copy), get_rates(copy)
    for pos, info in zip(order.positions, positions):
        info["alternatives"] = []
        try:
            features = position_features(pos)
        except ServiceError:
            continue
        drawing = (pos.data or {}).get("drawing") or {}
        # очередь станка перед этой позицией: другие заказы и позиции этого расчёта, стоящие в очереди раньше
        free = dict(busy)
        for slot in plan.slots:
            if slot.position_id not in later[pos.id]:
                free[slot.machine_code] = max(free.get(slot.machine_code) or slot.end, slot.end)
        tech = features.technology
        known = same_part(journal, pos.designation, drawing.get("designation"))  # эта деталь в журнале
        for m in machines:
            if tech not in m.technologies or not allowed_machines(features, [m]):
                continue  # другая технология — меняется в «Параметрах позиций»
            on_m = [e for e in journal if e.machine_code == m.code and e.technology == tech]
            choice = same_part(on_m, pos.designation, drawing.get("designation"))
            if choice:
                source = "эта деталь на этом станке (журнал)"
            elif known:
                choice, source = known, f"время этой детали с {known.entry.machine_code} (журнал)"
            else:
                choice, own = machine_route(journal, features, m.code, tech, pos.designation, drawing.get("designation"))
                if choice is None:
                    continue
                source = "по похожим деталям этого станка" if own else "по похожим деталям (на этом станке в журнале нет)"
            machine_h = sum(n.batch_hours(pos.quantity) for n in choice.route if n.stage.group is StageGroup.MACHINE)
            bench_h = sum(n.batch_hours(pos.quantity) for n in choice.route if n.stage.group is StageGroup.BENCH)
            start = MACHINE_CALENDAR.normalize(max(base, free.get(m.code) or base))
            end = MACHINE_CALENDAR.add_hours(start, machine_h)
            info["alternatives"].append({
                "machine": m.code, "model": m.model, "technology": tech, "analog": choice.entry.part_code,
                "source": source, "machine_hours": round(machine_h, 2), "bench_hours": round(bench_h, 2),
                "cost": round(machine_h * rates.machine + bench_h * rates.bench, 2),
                "free_from": start.isoformat(), "ready": end.isoformat(), "current": m.code == pos.machine_code})
        alts = info["alternatives"]
        if len(alts) > 1:
            fastest, cheapest = min(a["ready"] for a in alts), min(a["cost"] for a in alts)
            for a in alts:
                a["fastest"] = a["ready"] == fastest and any(b["ready"] != fastest for b in alts)
                a["cheapest"] = abs(a["cost"] - cheapest) < 0.01 and any(abs(b["cost"] - cheapest) >= 0.01 for b in alts)
        alts.sort(key=lambda a: a["ready"])


def calculate(session: Session, actor: Actor, files: list[UploadedFile], work_dir: Path | str, quantity: int = 1,
              desired_date: date | None = None, start_date: date | None = None, customer: str = "",
              quantities: list[int] | str | None = None) -> QuickResult:
    """quantities — ступени количества (до 4): цена и срок по каждой позиции для 1 / 10 / 50 шт."""
    require(actor.role, Action.UPLOAD_ORDER)
    quantities = _quantities(quantities)
    validate_quantity(quantity)
    _cleanup(work_dir)
    token = uuid.uuid4().hex
    folder = _folder(work_dir, token)
    (folder / "files").mkdir(parents=True)
    for f in files:
        if f.filename:
            (folder / "files" / Path(f.filename.replace("\\", "/")).name).write_bytes(f.content)
    params = {"quantity": quantity, "desired_date": desired_date.isoformat() if desired_date else None,
              "start_date": (start_date or date.today()).isoformat(), "customer": customer, "quantities": quantities}
    (folder / "params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")

    try:
        summary = _run(session, actor, folder, params)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    _save(folder, params, summary)
    return QuickResult(token, summary)


def _save(folder: Path, params: dict, summary: dict) -> None:
    summary["params"] = params
    (folder / "params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def _run(session: Session, actor: Actor, folder: Path, params: dict) -> dict:
    """Приём заказа и правки пользователя на временной копии базы; рабочая база не меняется."""
    tmp = Path(tempfile.mkdtemp(prefix="quick_"))
    engine = None
    try:
        _copy_database(session, tmp / "copy.db")
        engine = make_engine(f"sqlite:///{(tmp / 'copy.db').as_posix()}")
        with sessionmaker(bind=engine)() as copy:
            desired = params.get("desired_date")
            order = intake_service.quick_intake(copy, actor, _stored_files(folder), tmp / "uploads",
                                                customer=params.get("customer") or "",
                                                desired_date=date.fromisoformat(desired) if desired else None,
                                                start_date=date.fromisoformat(params["start_date"]),
                                                quantity=int(params.get("quantity") or 1))
            apply_overrides(copy, order, params.get("overrides") or {})
            return _summary(copy, actor, order, params.get("quantities"))
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


EDITABLE = ("quantity", "material", "dims", "tolerance_grade", "complexity", "technology", "machine",
            "extra_machines", "route")


def apply_overrides(session: Session, order, overrides: dict) -> None:
    """Правки параметров позиций (быстрый расчёт или запуск его как заказа): те же действия, что у технолога."""
    from app.domain.enums import CountMode, Stage
    from app.services import order_service, tech_service
    from app.services.auto_service import SYSTEM

    reason = "правка в быстром расчёте"
    for pos in order.positions:
        ov = overrides.get(pos.designation) or {}
        if "quantity" in ov:
            order_service.set_quantity(session, SYSTEM, pos.id, int(ov["quantity"]))
        data = {k: ov[k] for k in ("material", "dims", "tolerance_grade", "technology") if k in ov}
        if data:
            tech_service.update_data(session, SYSTEM, pos.id, data, reason)
        if "complexity" in ov:
            tech_service.set_complexity(session, SYSTEM, pos.id, int(ov["complexity"]), reason)
        if ov.get("machine") and ov["machine"] != pos.machine_code:
            from app.services.reference_service import domain_machines
            machine = next((m for m in domain_machines(session) if m.code == ov["machine"]), None)
            technology = (pos.data or {}).get("technology") or "milling"
            if machine and machine.technologies and technology not in machine.technologies:
                # токарный станок для фрезерной детали и наоборот — технология меняется вместе со станком
                tech_service.update_data(session, SYSTEM, pos.id,
                                         {"technology": sorted(machine.technologies)[0]}, reason)
            tech_service.assign_machine(session, SYSTEM, pos.id, ov["machine"])
        if "extra_machines" in ov:
            wanted = {e["machine"]: e for e in ov["extra_machines"]}
            for e in (pos.data or {}).get("extra_machines") or []:
                if e["machine"] not in wanted:
                    tech_service.remove_machine(session, SYSTEM, pos.id, e["machine"])
            for code, e in wanted.items():
                if code not in {x["machine"] for x in (pos.data or {}).get("extra_machines") or []}:
                    tech_service.add_machine(session, SYSTEM, pos.id, code)
                if e.get("setup") is not None or e.get("machining") is not None:
                    current = next(x for x in pos.data["extra_machines"] if x["machine"] == code)
                    tech_service.set_extra_machine_time(
                        session, SYSTEM, pos.id, code, float(e.get("setup", current.get("setup") or 0)),
                        float(e.get("machining", current.get("machining") or 0)), reason)
        for stage, hours in (ov.get("route") or {}).items():
            mode = next((s["count_mode"] for s in pos.route if s["stage"] == stage), None)
            mode = mode or (CountMode.PER_BATCH.value if stage in ("setup", "inspection")
                            else CountMode.ONCE.value if stage == "programming" else CountMode.PER_PIECE.value)
            tech_service.edit_route_stage(session, SYSTEM, pos.id, Stage(stage), float(hours), mode, reason)


def update_position(session: Session, actor: Actor, work_dir: Path | str, token: str, designation: str,
                    values: dict) -> dict:
    """Правка параметров позиции быстрого расчёта и пересчёт (ничего не записывается в рабочую базу).
    Сохраняются только изменённые значения; ошибка правки — расчёт остаётся прежним."""
    require(actor.role, Action.UPLOAD_ORDER)
    summary = load(work_dir, token)
    folder, params = _folder(work_dir, token), dict(summary["params"])
    current = next((p for p in summary.get("positions", []) if p["designation"] == designation), None)
    if current is None:
        raise ServiceError("Позиция расчёта не найдена")
    changes = dict((params.get("overrides") or {}).get(designation) or {})
    for key in EDITABLE:
        if key not in values:
            continue
        value = values[key]
        if key == "route":
            route = {s["stage"]: s["hours"] for s in current["route"]}
            changed = {st: h for st, h in value.items() if abs(float(h) - float(route.get(st, 0))) > 1e-9}
            if changed:
                changes["route"] = {**changes.get("route", {}), **changed}
        elif value != current.get(key):
            changes[key] = value
    params["overrides"] = {**(params.get("overrides") or {}), designation: changes}
    new_summary = _run(session, actor, folder, params)
    _save(folder, params, new_summary)
    return new_summary


def _quantities(value) -> list[int]:
    from app.domain.quantity_breaks import parse_quantities
    try:
        return parse_quantities(value)
    except ValueError as err:
        raise ServiceError(str(err)) from None


def set_quantities(session: Session, actor: Actor, work_dir: Path | str, token: str, value) -> dict:
    """Ступени количества быстрого расчёта: пересчёт на копии базы; ошибка — расчёт остаётся прежним."""
    require(actor.role, Action.UPLOAD_ORDER)
    summary = load(work_dir, token)
    params = {**summary["params"], "quantities": _quantities(value)}
    new_summary = _run(session, actor, _folder(work_dir, token), params)
    _save(_folder(work_dir, token), params, new_summary)
    return new_summary


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
    if params.get("overrides"):
        apply_overrides(session, order, params["overrides"])
        from app.services.history_service import record
        record(session, actor, "order", order.id, "quick_overrides", None, params["overrides"],
               "правки из быстрого расчёта")
        session.commit()
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
