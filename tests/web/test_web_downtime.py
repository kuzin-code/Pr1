"""Веб: причина отклонения при «Закончил», исправление мастером, сводка на главной."""
import re

from tests.web.test_web_operations import approve, op_ids


def _to_setup(login):
    pid = approve(login)
    cutter = login("zagotovitel")
    (cut,) = op_ids(cutter.get("/work").text)
    cutter.post(f"/operations/{cut}/start", data={"at": "2026-10-19T09:00"})
    cutter.post(f"/operations/{cut}/finish", data={"at": "2026-10-19T09:30"})
    operator = login("operator")
    (setup,) = op_ids(operator.get("/work").text)
    operator.post(f"/operations/{setup}/start", data={"at": "2026-10-19T10:00"})
    return pid, operator, setup


def test_work_page_has_reason_buttons(login):
    _, operator, setup = _to_setup(login)
    page = operator.get("/work").text
    assert 'class="w-reason' in page and 'name="reason" value="repair"' in page
    assert "ждал заготовку" in page and 'name="note"' in page


def test_finish_without_reason_over_plan_is_refused(login):
    _, operator, setup = _to_setup(login)
    resp = operator.post(f"/operations/{setup}/finish", data={"at": "2026-10-23T15:00"})
    assert resp.status_code == 303
    assert "выберите причину" in operator.get("/work").text
    operator.post(f"/operations/{setup}/finish", data={"at": "2026-10-23T15:00", "reason": "repair"})
    assert "Операция выполнена" in operator.get("/work").text


def test_master_fixes_reason_on_position_page(login):
    pid, operator, setup = _to_setup(login)
    operator.post(f"/operations/{setup}/finish", data={"at": "2026-10-23T15:00", "reason": "repair"})
    master = login("master")
    page = master.get(f"/positions/{pid}").text
    assert f'action="/operations/{setup}/reason"' in page and "ремонт" in page
    assert operator.post(f"/operations/{setup}/reason", data={"reason": "tool", "comment": "x"}).status_code == 403
    resp = master.post(f"/operations/{setup}/reason", data={"reason": "tool", "comment": ""})
    assert resp.headers["location"].startswith(f"/positions/{pid}")
    assert "причину" in master.get(f"/positions/{pid}").text
    master.post(f"/operations/{setup}/reason", data={"reason": "tool", "comment": "сломалась фреза"})
    page = master.get(f"/positions/{pid}").text
    assert re.search(r"badge warn\">инструмент", page)
    assert "Причина отклонения исправлена" in page


def test_dashboard_overruns_for_head_and_master_only(login):
    for user in ("head", "master"):
        assert "Часы сверх плана по причинам и станкам" in login(user).get("/dashboard").text
    assert "Часы сверх плана по причинам и станкам" not in login("technolog").get("/dashboard").text
