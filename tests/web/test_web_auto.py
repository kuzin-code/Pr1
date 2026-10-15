"""Веб: загрузка с количеством → готовый расчёт; технолог проверяет, вносит деталь в журнал."""
import re

from tests.fixtures.samples import make_box_step, make_pdf


def upload(login, name="КПУ.741645.001 Корпус", quantity="3"):
    files = [("files", (f"{name}.stp", make_box_step(84, 76, 26).encode(), "application/octet-stream")),
             ("files", (f"{name}.pdf", make_pdf(["Material: Poliacetal POM-S", "Bore 18H9"]), "application/pdf"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-19", "quantity": quantity}, files=files)
    assert resp.status_code == 303, resp.text
    page = login("master").get(resp.headers["location"]).text
    return int(re.search(r"/positions/(\d+)", page).group(1))


def test_upload_with_quantity_gives_ready_calculation(login):
    pid = upload(login)
    tech_page = login("technolog").get(f"/positions/{pid}").text
    assert "Проверка технолога" in tech_page
    assert "выбран системой" in tech_page
    assert "VF2" in tech_page
    assert "аналог подобран по технологии" in tech_page
    head_page = login("head").get(f"/positions/{pid}").text
    assert "₽" in head_page and "3 шт." in head_page


def test_auto_issues_shown_to_technologist(login):
    files = [("files", ("Без пары.stp", make_box_step().encode(), "application/octet-stream"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-19"}, files=files)
    page = login("master").get(resp.headers["location"]).text
    pid = int(re.search(r"/positions/(\d+)", page).group(1))
    assert "Замечания системы" in login("technolog").get(f"/positions/{pid}").text


def test_recalculate_and_add_to_journal(login):
    pid = upload(login)
    tech = login("technolog")
    assert tech.post(f"/positions/{pid}/auto").status_code == 303
    resp = tech.post(f"/positions/{pid}/journal")
    assert resp.status_code == 303
    assert "КПУ.741645.001 Корпус" in tech.get("/journal").text
    assert login("head").post(f"/positions/{pid}/journal").status_code == 403
