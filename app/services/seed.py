"""Первичная загрузка справочников (ТЗ, раздел 8). Повторный запуск ничего не дублирует."""
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import CountMode, Stage
from app.domain.models import CalibrationEntry, Dimensions, StageNorm
from app.services.security import hash_password
from app.storage.convert import entry_to_row
from app.storage.orm import JournalRow, MachineRow, Parameter, User


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_machines(session: Session, data: dict) -> None:
    existing = set(session.scalars(select(MachineRow.code)))
    for m in data["machines"]:
        if m["code"] not in existing:
            session.add(MachineRow(**{k: v for k, v in m.items() if not k.startswith("_")}))


def _seed_parameters(session: Session, data: dict) -> None:
    for key in ("rates", "schedule", "complexity_scale", "preferred_machines"):
        if session.get(Parameter, key) is None:
            session.add(Parameter(key=key, value=data[key]))
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
    if demo_journal:
        _seed_journal(session, _load(seed_dir / "journal_demo.json"))
    session.commit()
