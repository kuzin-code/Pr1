"""Шаблоны Jinja2 и форматирование значений."""
from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.domain.access import Action, can
from app.domain.enums import (
    CountMode,
    FactKind,
    PositionStatus,
    Role,
    Stage,
    STAGE_LABELS,
    STATUS_LABELS,
)
from app.web.deps import pop_flashes

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Версия статики: меняется при изменении style.css, чтобы браузер не брал старый файл из кэша.
STATIC_VERSION = str(int((Path(__file__).parent / "static" / "style.css").stat().st_mtime))


def money(value) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", " ") + " ₽"


def hours(value) -> str:
    return "—" if value is None else f"{value:.2f}".rstrip("0").rstrip(".") + " ч"


def dt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def short(value, limit: int = 80) -> str:
    if value is None:
        return "—"
    text = ", ".join(map(str, value)) if isinstance(value, list) and all(
        not isinstance(v, (dict, list)) for v in value) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def dims(values) -> str:
    return " × ".join(f"{v:g}" for v in values) if values else "—"


templates.env.filters.update(money=money, hours=hours, dt=dt, short=short, dims=dims)
templates.env.globals.update(
    Stage=Stage, CountMode=CountMode, FactKind=FactKind, PositionStatus=PositionStatus,
    STAGE_LABELS=STAGE_LABELS, STATUS_LABELS=STATUS_LABELS,
    stage_label=lambda v: Stage(v).label, status_label=lambda v: PositionStatus(v).label,
    count_label=lambda v: CountMode(v).label,
    static_version=STATIC_VERSION,
)


def render(request: Request, name: str, status_code: int = 200, **context):
    user = request.session.get("user")
    role = Role(user["role"]) if user else None
    context.update(
        user=user,
        role=role,
        can=lambda action: role is not None and can(role, Action(action)),
        flashes=pop_flashes(request),
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)
