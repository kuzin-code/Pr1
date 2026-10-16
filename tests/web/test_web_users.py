"""Веб: смена пароля, управление учётными записями (IT), обязательная смена временного пароля."""
from fastapi.testclient import TestClient


def test_demo_password_warning_and_change(login):
    tech = login("technolog")
    assert "демо-парол" in tech.get("/dashboard").text
    resp = tech.post("/account/password", data={"old": "demo", "new": "Технолог-2026", "repeat": "Технолог-2026"})
    assert resp.status_code == 303
    assert "демо-парол" not in tech.get("/dashboard").text


def test_password_repeat_must_match(login):
    tech = login("technolog")
    tech.post("/account/password", data={"old": "demo", "new": "Технолог-2026", "repeat": "Другой-2026"})
    assert "не совпадают" in tech.get("/account").text


def test_it_creates_user_who_must_change_password(app, login):
    it = login("it")
    assert it.get("/users").status_code == 200
    resp = it.post("/users", data={"username": "slesar2", "full_name": "Слесарь 2", "role": "locksmith",
                                   "password": "Временный-1"})
    assert resp.status_code == 303
    assert "slesar2" in it.get("/users").text
    new = TestClient(app, follow_redirects=False)
    new.post("/login", data={"username": "slesar2", "password": "Временный-1"})
    assert new.get("/work").headers["location"] == "/account"  # сначала — смена временного пароля
    new.post("/account/password", data={"old": "Временный-1", "new": "Слесарь-пароль", "repeat": "Слесарь-пароль"})
    assert new.get("/work").status_code == 200


def test_it_resets_and_disables(app, login):
    it = login("it")
    it.post("/users/master/reset", data={"password": "Сброс-пароль-1"})
    it.post("/users/master/active", data={"active": "0"})
    client = TestClient(app, follow_redirects=False)
    assert client.post("/login", data={"username": "master", "password": "Сброс-пароль-1"}).status_code == 401
    it.post("/users/master/active", data={"active": "1"})
    assert client.post("/login", data={"username": "master", "password": "Сброс-пароль-1"}).status_code == 303


def test_users_page_only_for_it(login):
    for user in ("technolog", "head", "master"):
        assert login(user).get("/users").status_code == 403
    assert login("head").get("/account").status_code == 200
