"""Веб: «срочно ↑» / «обычный» в очереди /plan, причина обязательна, сдвиг сроков и уведомления."""
from app.services import plan_service
from tests.web.test_web_flow import create_order, prepare


def _approved(login):
    ids = []
    for _ in range(2):
        _, pid = create_order(login)
        prepare(login, pid)
        login("technolog").post(f"/positions/{pid}/submit")
        login("head").post(f"/positions/{pid}/approve")
        ids.append(pid)
    return ids


def test_plan_has_priority_buttons_for_master_and_head(login):
    _, second = _approved(login)
    for user in ("master", "head"):
        page = login(user).get("/plan").text
        assert f'action="/positions/{second}/priority"' in page and "срочно ↑" in page
    assert f'action="/positions/{second}/priority"' not in login("technolog").get("/plan").text
    assert login("technolog").post(f"/positions/{second}/priority",
                                   data={"action": "urgent", "reason": "x"}).status_code == 403


def test_urgent_needs_reason_and_reports_shift(login, app):
    first, second = _approved(login)
    master = login("master")
    master.post(f"/positions/{second}/priority", data={"action": "urgent", "reason": ""})
    assert "причину" in master.get("/plan").text
    resp = master.post(f"/positions/{second}/priority", data={"action": "urgent", "reason": "звонил заказчик"})
    assert resp.status_code == 303
    page = master.get("/plan").text
    assert "Сдвинулись сроки" in page and "→" in page and "срочно</span>" in page
    with app.state.session_factory() as db:
        plan = plan_service.current_plan(db)
        assert plan.positions[second].machine_start < plan.positions[first].machine_start
    assert "Изменён приоритет" in login("manager").get("/notifications").text
    master.post(f"/positions/{second}/priority", data={"action": "normal", "reason": "отбой"})
    assert "обычный</span>" in master.get("/plan").text
