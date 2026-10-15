"""Оформление страницы входа: фон-фотография участка (ТЗ 1.1, НФТ-10)."""
from fastapi.testclient import TestClient

BG = "/static/img/login-bg.jpg"


def test_login_page_uses_background_photo(app):
    page = TestClient(app).get("/login").text
    assert 'class="login-page"' in page
    assert "Планирование загрузки" in page


def test_background_photo_served_locally_and_light(app):
    resp = TestClient(app).get(BG)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) < 400_000  # быстрая загрузка по сети участка


def test_background_referenced_from_css(app):
    css = TestClient(app).get("/static/style.css").text
    assert "img/login-bg.jpg" in css


def test_error_shown_on_login_page(app):
    resp = TestClient(app).post("/login", data={"username": "x", "password": "y"})
    assert 'class="login-page"' in resp.text and "Неверное имя" in resp.text
