"""Вход и права по ролям через веб-интерфейс (п. 4.5)."""
import pytest
from fastapi.testclient import TestClient

ROLES = ["technolog", "master", "operator", "head", "it", "management"]

PAGES = {
    "/orders": {"technolog", "master", "head"},
    "/orders/new": {"master"},
    "/plan": {"technolog", "master", "head", "management"},
    "/reference": {"technolog", "head", "it"},
    "/journal": {"technolog", "master", "head", "it"},
    "/history": {"technolog", "master", "head"},
    "/notifications": {"master"},
    "/facts": {"master", "operator"},
}


def test_unauthenticated_redirects_to_login(app):
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/orders")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_wrong_password(app):
    client = TestClient(app, follow_redirects=False)
    resp = client.post("/login", data={"username": "master", "password": "bad"})
    assert resp.status_code == 401
    assert "Неверное имя" in resp.text


@pytest.mark.parametrize("user", ROLES)
@pytest.mark.parametrize("page", list(PAGES))
def test_page_access_by_role(login, user, page):
    status = login(user).get(page).status_code
    assert status == (200 if user in PAGES[page] else 403)


@pytest.mark.parametrize("user,target", [
    ("operator", "/facts"), ("it", "/reference"), ("management", "/plan"), ("master", "/orders"),
])
def test_home_redirect_by_role(login, user, target):
    resp = login(user).get("/")
    assert resp.headers["location"] == target


def test_rates_change_only_by_it(login):
    data = {"machine": "2100", "bench": "1100", "reason": "индексация"}
    assert login("head").post("/rates", data=data).status_code == 403
    assert login("it").post("/rates", data=data).status_code == 303
    page = login("head").get("/reference").text
    assert "2 100 ₽" in page


def test_rates_hidden_from_technologist(login):
    assert "Ставка машинного времени" not in login("technolog").get("/reference").text
    assert "Ставка машинного времени" in login("head").get("/reference").text


def test_logout(login):
    client = login("master")
    client.post("/logout")
    assert client.get("/orders").status_code == 303
