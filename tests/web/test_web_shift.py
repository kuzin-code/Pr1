"""Веб: сменное задание на станок — ссылка на Ганте, страница, права, печать."""
import re

from tests.web.test_web_operations import approve


def _first_day(page):
    return re.search(r'<input type="date" name="date" value="([\d-]+)"', page).group(1)


def test_gantt_links_to_shift_task(login):
    approve(login)
    page = login("master").get("/plan").text
    assert 'href="/plan/shift?machine=VF2"' in page and "Сменное задание" in page


def test_shift_page_lists_operations(login, app):
    pid = approve(login)
    from app.services import plan_service
    with app.state.session_factory() as db:
        slot = next(s for s in plan_service.current_plan(db).slots if s.position_id == pid)
    day = slot.start.date().isoformat()
    page = login("master").get(f"/plan/shift?machine=VF2&date={day}").text
    assert "Сменное задание" in page and "Наладка" in page and "window.print()" in page
    assert 'data-label="Часы в смену"' in page  # на телефоне — карточки
    assert _first_day(page) == day


def test_shift_rights_for_specialists(login):
    approve(login)
    assert login("operator").get("/plan/shift?machine=VF2&date=2026-10-12").status_code == 200
    assert login("zagotovitel").get("/plan/shift?machine=VF2&date=2026-10-12").status_code == 403
    assert "Сменное задание" in login("operator").get("/work").text


def test_unknown_machine_and_bad_date(login):
    resp = login("master").get("/plan/shift?machine=NOPE")
    assert resp.status_code == 303
    assert login("master").get("/plan/shift?machine=VF2&date=abc").status_code == 200
