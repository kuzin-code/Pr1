"""Проверка технологичности позиции по STEP (dfm.py): расчёт, хранение в данных позиции, снятие технологом."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.dfm import DfmCheck, DfmRules, DfmStatus, StepFeatures, active_warnings, check_all
from app.domain.history import require_reason
from app.domain.models import Dimensions
from app.parsers.step_features import read_features
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.reference_service import domain_machines
from app.storage.orm import Parameter, PositionRow

ISSUE_PREFIX = "Технологичность: "


def rules(session: Session) -> DfmRules:
    row = session.get(Parameter, "dfm_rules")
    return DfmRules.from_dict(row.value if row else None)


def features(pos: PositionRow) -> StepFeatures | None:
    """Признаки STEP позиции; у позиций, считанных до проверки, читаются из файла один раз и запоминаются."""
    step = dict((pos.read_data or {}).get("step") or {})
    if "features" not in step:
        if not pos.step_path:
            return None
        found = read_features(pos.step_path)
        step["features"] = found.to_dict() if found else None
        pos.read_data = {**(pos.read_data or {}), "step": step}
    return StepFeatures.from_dict(step["features"])


def evaluate(session: Session, pos: PositionRow, data: dict) -> list[str]:
    """Проверки записываются в data["dfm"]; возвращаются замечания по неснятым предупреждениям."""
    dims = Dimensions.from_list(data.get("dims"))
    checks = check_all(dims, data.get("technology") or "milling", domain_machines(session), features(pos),
                       rules(session))
    data["dfm"] = [c.to_dict() for c in checks]
    return [f"{ISSUE_PREFIX}{c.detail}" for c in active_warnings(checks, data.get("dfm_dismissed") or {})]


def checks_of(pos: PositionRow) -> list[DfmCheck]:
    return [DfmCheck.from_dict(c) for c in (pos.data or {}).get("dfm") or []]


def dismiss(session: Session, actor: Actor, position_id: int, code: str, reason: str) -> None:
    """Технолог снимает предупреждение с причиной (в историю); при новом тексте предупреждение вернётся."""
    require(actor.role, Action.DISMISS_DFM)
    reason = require_reason(reason)
    pos = session.get(PositionRow, position_id)
    if pos is None:
        raise ServiceError("Позиция не найдена")
    check = next((c for c in checks_of(pos) if c.code == code and c.status is DfmStatus.WARN), None)
    if check is None:
        raise ServiceError("Такого предупреждения технологичности у позиции нет")
    data = dict(pos.data or {})
    data["dfm_dismissed"] = {**(data.get("dfm_dismissed") or {}),
                             code: {"detail": check.detail, "reason": reason, "by": actor.username,
                                    "at": datetime.now().isoformat(timespec="minutes")}}
    data["auto_issues"] = [i for i in data.get("auto_issues") or [] if i != f"{ISSUE_PREFIX}{check.detail}"]
    pos.data = data
    record(session, actor, "position", pos.id, "dfm_dismiss", check.detail, None, reason)
    session.commit()
