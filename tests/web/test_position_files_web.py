"""Веб: чертёж и 3D-модель из «Моих операций», ссылки уведомлений специалистов, метка версии статики."""
import os
import re

from tests.web.test_web_flow import create_order
from tests.web.test_web_operations import approve, op_ids, press


def test_operator_opens_drawing_and_model_of_own_position(login):
    pid = approve(login)
    _, other = create_order(login, "Планка-2")  # не утверждена — у оператора операций нет
    cutter = login("zagotovitel")
    (cut,) = op_ids(cutter.get("/work").text)
    press(cutter, cut, "start", "2026-10-19T09:00")
    press(cutter, cut, "finish", "2026-10-19T10:00")  # у оператора наладка — «можно начинать»
    operator = login("operator")
    work = operator.get("/work").text
    assert f"/positions/{pid}/file/pdf" in work and f"/positions/{pid}/model" in work
    assert operator.get(f"/positions/{pid}/file/pdf").status_code == 200
    assert operator.get(f"/positions/{pid}/file/step").status_code == 200
    model = operator.get(f"/positions/{pid}/model")
    assert model.status_code == 200 and 'href="/work"' in model.text
    assert operator.get(f"/positions/{pid}").status_code == 403  # карточка позиции — по-прежнему нет
    for url in (f"/positions/{other}/file/pdf", f"/positions/{other}/file/step", f"/positions/{other}/model"):
        assert operator.get(url).status_code == 403
    for user in ("zagotovitel", "slesar", "kontroler"):
        assert login(user).get(f"/positions/{pid}/file/pdf").status_code == 200
    assert login("technolog").get(f"/positions/{other}/file/pdf").status_code == 200  # как раньше


def test_notification_link_for_specialist_leads_to_work(login):
    pid = approve(login)
    cutter = login("zagotovitel")
    (cut,) = op_ids(cutter.get("/work").text)
    press(cutter, cut, "start", "2026-10-19T09:00")
    press(cutter, cut, "finish", "2026-10-19T10:00")  # оператору: «Наладка: можно начинать»
    page = login("operator").get("/notifications").text
    assert f'href="/positions/{pid}"' not in page and 'href="/work">открыть' in page
    assert f'href="/positions/{pid}">открыть' in login("technolog").get("/notifications").text


def test_static_version_follows_every_css(tmp_path):
    from app.web.templating import static_version

    (tmp_path / "style.css").write_text("a{}")
    (tmp_path / "work.css").write_text("b{}")
    os.utime(tmp_path / "style.css", (1_700_000_000, 1_700_000_000))
    os.utime(tmp_path / "work.css", (1_800_000_000, 1_800_000_000))
    assert static_version(tmp_path) == "1800000000"


def test_work_css_uses_static_version(login):
    from app.web.templating import STATIC_VERSION

    approve(login)
    page = login("operator").get("/work").text
    assert re.search(rf'/static/work\.css\?v={STATIC_VERSION}"', page)
    assert f"/static/style.css?v={STATIC_VERSION}" in page
