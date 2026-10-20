"""Карточка позиции: смена станка и второй станок в техпроцессе — время и стоимость пересчитываются."""
import re

from fastapi.testclient import TestClient

from tests.fixtures.samples import make_box_step, make_pdf


def client_for(app, user):
    c = TestClient(app)
    c.post("/login", data={"username": user, "password": "demo"})
    return c


def test_add_edit_remove_second_machine(app):
    page = client_for(app, "manager").post("/orders", files=[
        ("files", ("P-1.step", make_box_step(90, 50, 20).encode())), ("files", ("P-1.pdf", make_pdf(["Bore 10H9"])))]).text
    pid = re.search(r'href="/positions/(\d+)"', page).group(1)
    tech = client_for(app, "technolog")
    assert "Второй станок в техпроцессе" in tech.get(f"/positions/{pid}").text
    page = tech.post(f"/positions/{pid}/extra-machine", data={"code": "ST20Y"}).text
    assert "добавлен в техпроцесс" in page and "Обработка на станке — Haas ST-20Y" in page
    page = tech.post(f"/positions/{pid}/extra-machine/ST20Y/time",
                     data={"setup": "1,5", "machining": "0.2", "reason": "по опыту"}).text
    assert "время технолога" in page
    page = tech.post(f"/positions/{pid}/extra-machine/ST20Y/remove").text
    assert "— Haas ST-20Y" not in page
