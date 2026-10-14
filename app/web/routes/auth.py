"""Вход и выход; у каждой роли своя учётная запись (п. 4.5)."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app.domain.enums import Role
from app.services.reference_service import authenticate
from app.web.deps import current_actor, get_db, redirect
from app.web.templating import render

router = APIRouter()

HOME = {Role.OPERATOR: "/facts", Role.IT: "/reference", Role.MANAGEMENT: "/plan"}


@router.get("/")
def home(request: Request):
    actor = current_actor(request)
    return redirect(HOME.get(actor.role, "/orders"))


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    user = authenticate(db, username.strip(), password)
    if user is None:
        return render(request, "login.html", status_code=401, error="Неверное имя пользователя или пароль")
    request.session.clear()
    request.session["user"] = {"username": user.username, "role": user.role, "name": user.full_name}
    return redirect("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")
