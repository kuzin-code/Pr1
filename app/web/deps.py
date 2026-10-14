"""Зависимости веб-слоя: сессия БД, текущий пользователь, сообщения, выполнение действий."""
from collections.abc import Callable, Iterator

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.services.actor import Actor


class NotLoggedIn(Exception):
    pass


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def current_actor(request: Request) -> Actor:
    user = request.session.get("user")
    if not user:
        raise NotLoggedIn()
    return Actor(user["username"], Role(user["role"]))


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", [])
    request.session["flash"] = [*request.session["flash"], [kind, message]]


def pop_flashes(request: Request) -> list[list[str]]:
    return request.session.pop("flash", [])


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def act(request: Request, db: Session, back: str, fn: Callable[[], object],
        ok: str | None = None) -> RedirectResponse:
    """Выполняет действие формы; ошибка бизнес-правила показывается пользователю на той же странице.

    AccessDenied (PermissionError) не перехватывается и даёт 403.
    """
    try:
        fn()
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect(back)
    if ok:
        flash(request, ok)
    return redirect(back)
