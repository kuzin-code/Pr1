"""Первичная загрузка справочников (ТЗ, раздел 8). Повторный запуск ничего не дублирует."""
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.blank import BlankRules
from app.domain.enums import CountMode, Stage
from app.domain.operations import normalize_operations
from app.domain.models import CalibrationEntry, Dimensions, StageNorm
from app.services.security import hash_password
from app.storage.convert import entry_to_row
from app.storage.orm import HistoryRow, JournalRow, MachineRow, Parameter, User


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_machines(session: Session, data: dict) -> None:
    existing = {m.code: m for m in session.scalars(select(MachineRow))}
    for m in data["machines"]:
        if m["code"] not in existing:
            session.add(MachineRow(**{k: v for k, v in m.items() if not k.startswith("_")}))
    # Операции, внесённые до разделения обработки на фрезерную и токарную, приводятся к новому перечню.
    for row in existing.values():
        normalized = normalize_operations(row.operations, row.technologies)
        if row.operations is not None and normalized != row.operations:
            row.operations = normalized


OLD_ALLOWANCES = {"sheet_side_allowance_mm": 3, "sheet_thickness_side_allowance_mm": 3,
                  "bar_diameter_side_allowance_mm": 3, "bar_length_side_allowance_mm": 3}


def _old_allowances(session: Session, value: dict) -> bool:
    """Припуски — прежние по умолчанию (3 мм на сторону) и в справочнике их никто не менял."""
    if any(float(value.get(k, -1)) != v for k, v in OLD_ALLOWANCES.items()):
        return False
    return session.scalar(select(HistoryRow.id).where(HistoryRow.entity == "parameters",
                                                      HistoryRow.entity_id == "blank_rules").limit(1)) is None


def _seed_parameters(session: Session, data: dict) -> None:
    for key in ("rates", "schedule", "complexity_scale", "preferred_machines", "blank_rules"):
        if key in data and session.get(Parameter, key) is None:
            session.add(Parameter(key=key, value=data[key]))
    # Припуски заготовки прежнего вида заменяются: до рядов по группам материала (не читаются) и прежние
    # «3 мм на сторону» (решение 29.10.2026 — 1,5 мм на сторону), если их не меняли в справочнике (нет истории).
    blank = session.get(Parameter, "blank_rules")
    if blank is not None and "blank_rules" in data and (BlankRules.from_dict(blank.value) is None
                                                       or _old_allowances(session, blank.value)):
        blank.value = data["blank_rules"]
    if session.get(Parameter, "journal_version") is None:
        session.add(Parameter(key="journal_version", value=1))


def _seed_users(session: Session, data: dict) -> None:
    existing = set(session.scalars(select(User.username)))
    for u in data["users"]:
        if u["username"] not in existing:
            session.add(User(username=u["username"], full_name=u["full_name"], role=u["role"],
                             password_hash=hash_password(u["password"])))


def _seed_journal(session: Session, data: dict) -> None:
    if session.scalar(select(JournalRow.id).limit(1)) is not None:
        return
    for e in data["entries"]:
        entry = CalibrationEntry(
            part_code=e["part_code"], name=e["name"], machine_code=e["machine_code"],
            material=e["material"], dims=Dimensions(*e["dims"]), tolerance_grade=e["tolerance_grade"],
            complexity=e["complexity"], technology=e["technology"],
            stages=[StageNorm(Stage(s), h, CountMode(c)) for s, h, c in e["stages"]],
        )
        session.add(entry_to_row(entry))


def seed_reference(session: Session, seed_dir: Path | str, demo_journal: bool = True) -> None:
    seed_dir = Path(seed_dir)
    _seed_machines(session, _load(seed_dir / "machines.json"))
    _seed_parameters(session, _load(seed_dir / "parameters.json"))
    _seed_users(session, _load(seed_dir / "users.json"))
    rules = seed_dir / "dfm_rules.json"  # пороги проверки технологичности по STEP
    if rules.exists() and session.get(Parameter, "dfm_rules") is None:
        session.add(Parameter(key="dfm_rules", value={k: v for k, v in _load(rules).items() if not k.startswith("_")}))
    if demo_journal:
        _seed_journal(session, _load(seed_dir / "journal_demo.json"))
    session.commit()
