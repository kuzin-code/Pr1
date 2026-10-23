"""Веб: технологичность по STEP — блок в карточке позиции и в быстром расчёте, снятие технологом."""
import re

from tests.fixtures.samples import make_pdf
from tests.fixtures.step_dfm import make_dfm_step
from tests.web.test_quick_web import client_for

DEEP = make_dfm_step(holes=[(20, 20, 1.5, 18)]).encode()


def _order(login):
    files = [("files", ("Плита-1.step", DEEP, "application/octet-stream")),
             ("files", ("Плита-1.pdf", make_pdf(["Material: D16T"]), "application/pdf"))]
    resp = login("master").post("/orders", data={"start_date": "2026-10-12"}, files=files)
    page = login("master").get(resp.headers["location"]).text
    return int(re.search(r"/positions/(\d+)", page).group(1))


def test_position_page_shows_checks_and_technologist_dismisses(login):
    pid = _order(login)
    tech = login("technolog")
    page = tech.get(f"/positions/{pid}").text
    assert "Технологичность по STEP" in page and "Глубина отверстий" in page and "предупреждение" in page
    assert f'action="/positions/{pid}/dfm/holes/dismiss"' in page
    assert f'action="/positions/{pid}/dfm/holes/dismiss"' not in login("master").get(f"/positions/{pid}").text
    assert login("master").post(f"/positions/{pid}/dfm/holes/dismiss", data={"reason": "x"}).status_code == 403
    tech.post(f"/positions/{pid}/dfm/holes/dismiss", data={"reason": ""})
    assert "причину" in tech.get(f"/positions/{pid}").text
    tech.post(f"/positions/{pid}/dfm/holes/dismiss", data={"reason": "сверлим с выводом"})
    page = tech.get(f"/positions/{pid}").text
    assert "Предупреждение снято" in page and "сверлим с выводом" in page and ">снято<" in page


def test_quick_result_shows_checks(app):
    c = client_for(app)
    resp = c.post("/quick", data={"quantity": "1"},
                  files=[("files", ("Q-1.step", DEEP)), ("files", ("Q-1.pdf", make_pdf(["Material: D16T"])))])
    assert resp.status_code == 200
    assert "Технологичность по STEP" in resp.text and "Глубина/диаметр больше 10" in resp.text
