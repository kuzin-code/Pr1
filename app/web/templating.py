"""Шаблоны Jinja2 и форматирование значений."""
from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.domain.access import Action, can
from app.domain.desired_date import check_desired
from app.domain.order_intake import MAX_QUANTITY
from app.domain.enums import (
    CountMode,
    FactKind,
    Operation,
    PositionStatus,
    Role,
    Stage,
    STAGE_LABELS,
    STATUS_LABELS,
)
from app.web.deps import pop_flashes

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
def static_version(folder: Path) -> str:
    """Версия статики — по самому свежему *.css (style.css, work.css…), чтобы браузер не брал старый файл
    из кэша."""
    return str(int(max((p.stat().st_mtime for p in Path(folder).glob("*.css")), default=0)))


STATIC_VERSION = static_version(Path(__file__).parent / "static")


def _quick_proposal_rows(values: dict) -> list[tuple[str, str]]:
    from app.services.quick_apply import describe
    return describe(values or {})


def money(value) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", " ") + " ₽"


def hours(value) -> str:
    return "—" if value is None else f"{value:.2f}".rstrip("0").rstrip(".") + " ч"


def dt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
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


templates.env.filters.update(money=money, hours=hours, dt=dt, short=short, dims=dims,
                             role_label=lambda v: Role(v).label)
templates.env.globals.update(check_desired=check_desired)
from app.services.proposal_docx import hours as kp_hours, money as kp_money  # noqa: E402
templates.env.globals.update(kp_money=kp_money, kp_hours=kp_hours)


def quick_bar(first: str, last: str, start: str, end: str) -> str:
    """Полоса занятости станка на общей шкале быстрого расчёта."""
    a, b = datetime.fromisoformat(first), datetime.fromisoformat(last)
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    span = max((b - a).total_seconds(), 1)
    left, width = (s - a).total_seconds() / span * 100, (e - s).total_seconds() / span * 100
    return (f'<div class="quick-track" title="{s:%d.%m %H:%M} — {e:%d.%m %H:%M}">'
            f'<span style="left:{left:.1f}%;width:{width:.1f}%"></span></div>')


templates.env.globals.update(quick_bar=quick_bar)
templates.env.globals.update(
    Stage=Stage, CountMode=CountMode, FactKind=FactKind, PositionStatus=PositionStatus,
    STAGE_LABELS=STAGE_LABELS, STATUS_LABELS=STATUS_LABELS,
    stage_label=lambda v: Stage(v).label, status_label=lambda v: PositionStatus(v).label,
    count_label=lambda v: CountMode(v).label,
    Operation=Operation, operation_label=lambda v: Operation(v).label,
    static_version=STATIC_VERSION, max_quantity=MAX_QUANTITY, quick_proposal_rows=_quick_proposal_rows,
)


def render(request: Request, name: str, status_code: int = 200, **context):
    user = request.session.get("user")
    role = Role(user["role"]) if user else None
    unread = 0
    if role is not None and can(role, Action.RECEIVE_NOTIFICATIONS):
        from app.services.plan_service import unread_count

        with request.app.state.session_factory() as session:
            unread = unread_count(session, role)
    context.update(
        user=user,
        role=role,
        unread=unread,
        can=lambda action: role is not None and can(role, Action(action)),
        flashes=pop_flashes(request),
        path=request.url.path,  # для подсветки текущего раздела в меню
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)
