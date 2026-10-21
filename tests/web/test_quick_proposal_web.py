"""Веб: запуск быстрого расчёта менеджером — предложение технологу в карточке позиции; без двойного заказа."""
import re

from tests.web.test_quick_web import calc, client_for

BLOCK = "Предложение из быстрого расчёта"


def launch_with_edits(app):
    manager = client_for(app)
    _, token = calc(manager)
    manager.post(f"/quick/{token}/position", data={
        "designation": "Q-1", "quantity": "5", "material": "12Х18Н10Т", "dim0": "90", "dim1": "50", "dim2": "20",
        "tolerance_grade": "", "complexity": "", "technology": "milling", "machine": "SMM", "extra_machine": ""})
    page = manager.post(f"/quick/{token}/launch").text
    assert "переданы технологу как предложение" in page
    again = manager.post(f"/quick/{token}/launch").text  # повторное нажатие
    assert "не найден" in again
    assert manager.get("/orders").text.count("ООО «Гамма»") == 1
    pid = int(re.search(r"/positions/(\d+)", page).group(1))
    return manager, pid


def test_technologist_sees_and_accepts_proposal(app):
    manager, pid = launch_with_edits(app)
    view = manager.get(f"/positions/{pid}").text
    assert BLOCK in view and "12Х18Н10Т" in view and "может технолог" in view and "Принять</button>" not in view
    tech = client_for(app, "technolog")
    page = tech.get(f"/positions/{pid}").text
    assert BLOCK in page and "Принять</button>" in page and "Отклонить</button>" in page
    no_reason = tech.post(f"/positions/{pid}/quick-proposal/accept", data={"reason": ""}).text
    assert "причину" in no_reason and BLOCK in no_reason
    done = tech.post(f"/positions/{pid}/quick-proposal/accept", data={"reason": "проверил"}).text
    assert "Предложение принято" in done and BLOCK not in done and "Haas Super Mini Mill" in done


def test_technologist_rejects_and_manager_cannot_decide(app):
    manager, pid = launch_with_edits(app)
    assert manager.post(f"/positions/{pid}/quick-proposal/accept", data={"reason": "x"}).status_code == 403
    tech = client_for(app, "technolog")
    done = tech.post(f"/positions/{pid}/quick-proposal/reject", data={"reason": "материал по чертежу Д16Т"}).text
    assert "Предложение отклонено" in done and BLOCK not in done
