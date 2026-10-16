"""Учётные записи: «Мой пароль» для всех, управление пользователями — IT."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.services import user_service as users
from app.services.actor import ServiceError
from app.web.deps import act, current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()


@router.get("/account")
def account(request: Request):
    current_actor(request)
    return render(request, "account.html")


@router.post("/account/password")
def change_password(request: Request, old: str = Form(""), new: str = Form(""), repeat: str = Form(""),
                    db: Session = Depends(get_db)):
    actor = current_actor(request)
    try:
        if new != repeat:
            raise ServiceError("Новый пароль и повтор не совпадают")
        users.change_password(db, actor, old, new)
    except ValueError as err:
        db.rollback()
        flash(request, str(err), "error")
        return redirect("/account")
    request.session["user"] = {**request.session["user"], "must_change": False, "weak": False}
    flash(request, "Пароль изменён")
    return redirect("/")


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    return render(request, "users.html", users=users.list_users(db, actor), roles=list(Role))


@router.post("/users")
def create_user(request: Request, username: str = Form(""), full_name: str = Form(""), role: str = Form(""),
                password: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, "/users", lambda: users.create_user(db, actor, username, full_name, role, password),
               "Учётная запись создана: при первом входе пользователь сменит временный пароль")


@router.post("/users/{username}/reset")
def reset(request: Request, username: str, password: str = Form(""), db: Session = Depends(get_db)):
    actor = current_actor(request)
    return act(request, db, "/users", lambda: users.reset_password(db, actor, username, password),
               f"Временный пароль для «{username}» задан")


@router.post("/users/{username}/active")
def set_active(request: Request, username: str, active: str = Form("1"), db: Session = Depends(get_db)):
    actor = current_actor(request)
    flag = active == "1"
    return act(request, db, "/users", lambda: users.set_active(db, actor, username, flag),
               f"Учётная запись «{username}» {'включена' if flag else 'отключена'}")

