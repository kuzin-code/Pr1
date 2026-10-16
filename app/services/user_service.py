"""Учётные записи: смена своего пароля; управление пользователями — IT (п. 4.5)."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.access import Action, require
from app.domain.enums import Role
from app.services.actor import Actor, ServiceError
from app.services.history_service import record
from app.services.security import hash_password, verify_password
from app.storage.orm import User

MIN_LENGTH = 8
USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")


def _user(session: Session, username: str) -> User:
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        raise ServiceError(f"Учётная запись «{username}» не найдена")
    return user


def _check_password(username: str, password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise ServiceError(f"Пароль должен быть не короче {MIN_LENGTH} символов")
    if username.casefold() in password.casefold():
        raise ServiceError("Пароль не должен содержать имя учётной записи")


def change_password(session: Session, actor: Actor, old: str, new: str) -> None:
    """Каждый пользователь меняет свой пароль, зная текущий."""
    user = _user(session, actor.username)
    if not verify_password(old, user.password_hash):
        raise ServiceError("Текущий пароль указан неверно")
    _check_password(user.username, new)
    if verify_password(new, user.password_hash):
        raise ServiceError("Новый пароль совпадает с текущим")
    user.password_hash = hash_password(new)
    user.must_change_password = False
    record(session, actor, "user", user.username, "password", None, None, "пароль изменён пользователем")
    session.commit()


def list_users(session: Session, actor: Actor) -> list[User]:
    require(actor.role, Action.MANAGE_USERS)
    return list(session.scalars(select(User).order_by(User.role, User.username)))


def create_user(session: Session, actor: Actor, username: str, full_name: str, role: str,
                password: str) -> User:
    """Новая учётная запись; при первом входе пользователь меняет временный пароль."""
    require(actor.role, Action.MANAGE_USERS)
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise ServiceError("Имя учётной записи: латиница, цифры, «_ . -», от 3 символов, начинается с буквы")
    if session.scalar(select(User.id).where(User.username == username)):
        raise ServiceError(f"Учётная запись «{username}» уже есть")
    try:
        role_value = Role(role).value
    except ValueError:
        raise ServiceError(f"Неизвестная роль «{role}»") from None
    _check_password(username, password)
    user = User(username=username, full_name=full_name.strip() or username, role=role_value,
                password_hash=hash_password(password), active=True, must_change_password=True)
    session.add(user)
    record(session, actor, "user", username, "create", None, {"role": role_value, "name": user.full_name})
    session.commit()
    return user


def reset_password(session: Session, actor: Actor, username: str, password: str) -> None:
    """IT задаёт временный пароль (например, если пользователь его забыл)."""
    require(actor.role, Action.MANAGE_USERS)
    user = _user(session, username)
    _check_password(username, password)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    record(session, actor, "user", username, "password", None, None, "временный пароль задан IT")
    session.commit()


def set_active(session: Session, actor: Actor, username: str, active: bool) -> None:
    """Отключённая учётная запись не может войти; история её действий сохраняется."""
    require(actor.role, Action.MANAGE_USERS)
    if username == actor.username and not active:
        raise ServiceError("Нельзя отключить себя")
    user = _user(session, username)
    if user.active is not False and active:
        return
    user.active = active
    record(session, actor, "user", username, "active", not active, active)
    session.commit()
