"""Учётные записи: смена своего пароля, управление пользователями (IT), отключение."""
import pytest

from app.domain.access import AccessDenied
from app.services import reference_service as ref
from app.services import user_service as users
from app.services.actor import ServiceError
from app.services.history_service import list_history
from tests.services.conftest import HEAD, IT, TECH


def test_change_own_password(session):
    users.change_password(session, TECH, "demo", "Новый-пароль-1")
    assert ref.authenticate(session, "technolog", "Новый-пароль-1") is not None
    assert ref.authenticate(session, "technolog", "demo") is None
    record = next(h for h in list_history(session, entity="user") if h.field == "password")
    assert record.new is None and "Новый" not in str(record.old)  # пароль в историю не пишется


@pytest.mark.parametrize("old,new,match", [
    ("неверный", "Длинный-пароль", "Текущий пароль"),
    ("demo", "коротко", "не короче 8"),
    ("demo", "demo", "не короче 8"),
    ("demo", "technolog-1", "имя"),
])
def test_password_rules(session, old, new, match):
    with pytest.raises(ServiceError, match=match):
        users.change_password(session, TECH, old, new)


def test_it_creates_resets_and_disables_users(session):
    users.create_user(session, IT, "slesar2", "Петров П. (слесарь)", "locksmith", "Временный-1")
    assert ref.authenticate(session, "slesar2", "Временный-1").must_change_password
    users.reset_password(session, IT, "slesar2", "Сброшенный-2")
    assert ref.authenticate(session, "slesar2", "Сброшенный-2") is not None
    users.set_active(session, IT, "slesar2", False)
    assert ref.authenticate(session, "slesar2", "Сброшенный-2") is None  # отключённый не входит
    users.set_active(session, IT, "slesar2", True)
    assert ref.authenticate(session, "slesar2", "Сброшенный-2") is not None


def test_user_rules(session):
    with pytest.raises(ServiceError, match="уже есть"):
        users.create_user(session, IT, "master", "Дубль", "master", "Временный-1")
    with pytest.raises(ServiceError, match="Неизвестная роль"):
        users.create_user(session, IT, "xx1", "X", "boss", "Временный-1")
    with pytest.raises(ServiceError, match="латиниц"):
        users.create_user(session, IT, "иван", "Иван", "master", "Временный-1")
    with pytest.raises(ServiceError, match="себя"):
        users.set_active(session, IT, "it", False)


def test_only_it_manages_users(session):
    for call in (lambda: users.list_users(session, HEAD),
                 lambda: users.create_user(session, TECH, "x2", "X", "master", "Временный-1"),
                 lambda: users.reset_password(session, HEAD, "master", "Временный-1")):
        with pytest.raises(AccessDenied):
            call()
    assert len(users.list_users(session, IT)) == 10


def test_must_change_cleared_after_own_change(session):
    users.create_user(session, IT, "kontroler2", "Контролёр 2", "inspector", "Временный-1")
    from app.services.actor import Actor
    from app.domain.enums import Role
    users.change_password(session, Actor("kontroler2", Role.INSPECTOR), "Временный-1", "Мой-пароль-9")
    assert not ref.authenticate(session, "kontroler2", "Мой-пароль-9").must_change_password
