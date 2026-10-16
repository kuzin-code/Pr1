"""Страница быстрого расчёта: результат без заказа; удалить, сообщить, запустить."""
import re

from fastapi.testclient import TestClient

from tests.fixtures.samples import make_box_step, make_pdf


def client_for(app, user="manager"):
    c = TestClient(app)
    c.post("/login", data={"username": user, "password": "demo"})
    return c


def files(stem="Q-1"):
    return [("files", (f"{stem}.step", make_box_step(90, 50, 20).encode())),
            ("files", (f"{stem}.pdf", make_pdf(["Bore 10H9"])))]


def calc(c):
    resp = c.post("/quick", data={"quantity": "3", "desired_date": "2030-01-15", "customer": "ООО «Гамма»"},
                  files=files())
    assert resp.status_code == 200, resp.text[:500]
    return resp, re.search(r"/quick/([0-9a-f]{32})/launch", resp.text).group(1)


def test_quick_calc_shows_price_dates_and_machines_without_order(app):
    c = client_for(app)
    assert "Перетащите сюда все файлы заказа" in c.get("/quick").text
    resp, _ = calc(c)
    for text in ("Цена с НДС", "Срок изготовления", "Желаемая дата", "Haas VF-2", "узкое место", "Заказ не создан"):
        assert text in resp.text
    assert "Заказов нет" in c.get("/orders").text


def test_launch_and_discard(app):
    c = client_for(app)
    _, token = calc(c)
    page = c.post(f"/quick/{token}/launch")
    assert "запущен" in page.text and "ООО «Гамма»" in c.get("/orders").text
    _, token = calc(c)
    c.post(f"/quick/{token}/discard")
    assert "не найден" in c.get(f"/quick/{token}").text


def test_notify_head(app):
    c = client_for(app)
    _, token = calc(c)
    assert "отправлена" in c.post(f"/quick/{token}/notify").text
    assert "Быстрый расчёт" in client_for(app, "head").get("/notifications").text


def test_operator_no_access(app):
    assert client_for(app, "operator").get("/quick").status_code == 403
