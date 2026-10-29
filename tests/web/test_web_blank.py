"""Веб: заготовка — карточка позиции, «Мои операции» заготовителя, уведомление, припуски в справочнике."""
from tests.web.test_web_flow import create_order, prepare
from tests.web.test_web_operations import approve, op_ids, press

SYSTEM_TEXT = "Д16Т, лист h45 мм, 155×61 мм — 10 шт."  # 152×58×40: 40+2×1,5 → 45 по ряду, 152+2×1,5, 58+2×1,5


def test_position_card_shows_blank_and_technologist_edits_it(login):
    _, pid = create_order(login)
    prepare(login, pid)
    tech = login("technolog")
    page = tech.get(f"/positions/{pid}").text
    assert 'id="blank"' in page and SYSTEM_TEXT in page and "предложено системой" in page
    for user in ("master", "head"):
        assert SYSTEM_TEXT in login(user).get(f"/positions/{pid}").text
    assert 'id="blank"' not in login("manager").get(f"/positions/{pid}").text  # менеджеру заготовка не нужна

    form = {"kind": "round", "d": "60", "length": "160", "quantity": "10", "note": "пруток со склада", "reason": ""}
    resp = tech.post(f"/positions/{pid}/blank", data=form)
    assert "причин" in tech.get(resp.headers["location"]).text.lower()  # без причины не сохраняется
    tech.post(f"/positions/{pid}/blank", data={**form, "reason": "по наличию"})
    page = tech.get(f"/positions/{pid}").text
    assert "Д16Т, круг Ø60 мм, L 160 мм — 10 шт." in page and "задано технологом" in page
    assert "по наличию" in page  # в истории корректировок
    tech.post(f"/positions/{pid}/auto")  # пересчёт не меняет заготовку технолога
    assert "Д16Т, круг Ø60 мм, L 160 мм — 10 шт." in tech.get(f"/positions/{pid}").text
    assert login("head").post(f"/positions/{pid}/blank", data={**form, "reason": "x"}).status_code == 403


def test_cutter_sees_blank_others_do_not(login):
    pid = approve(login)
    cutter = login("zagotovitel")
    page = cutter.get("/work").text
    assert SYSTEM_TEXT in page and "предложено системой" in page and "Начал" in page
    assert page.index(SYSTEM_TEXT) < page.index("Резка заготовки")  # первой строкой карточки
    assert SYSTEM_TEXT in login("master").get("/work").text
    for user in ("operator", "slesar", "kontroler"):
        assert SYSTEM_TEXT not in login(user).get("/work").text
    (cut,) = op_ids(page)
    press(cutter, cut, "start", "2026-10-19T09:00")
    assert SYSTEM_TEXT in cutter.get("/work").text  # и в работе — вместе с «Закончил»
    assert pid


def test_cutter_new_order_notification_has_blank(login):
    _, pid = create_order(login)
    assert "Заготовки:" in login("zagotovitel").get("/notifications").text


def test_reference_blank_rules(login):
    page = login("technolog").get("/reference").text
    assert 'id="blank-rules"' in page and "1,5" in page and "270" in page and "ГОСТ 21488-97" in page
    assert 'name="reason"' not in login("head").get("/reference").text.split('id="blank-rules"')[1].split("</div>\n")[0]
    form = {"sheet_side_allowance_mm": "5", "sheet_thickness_side_allowance_mm": "2",
            "bar_diameter_side_allowance_mm": "3", "bar_length_side_allowance_mm": "3", "size_step_mm": "1",
            "sheet_thicknesses_mm": "10 20 45 50", "round_steel": "10 20 30", "round_stainless": "",
            "round_aluminium": "10 20", "round_brass": "", "reason": "по складу"}
    assert login("head").post("/reference/blank-rules", data=form).status_code == 403
    assert login("technolog").post("/reference/blank-rules", data=form).headers["location"] == "/reference#blank-rules"
    _, pid = create_order(login)
    prepare(login, pid)
    assert "Д16Т, лист h45 мм, 162×68 мм — 10 шт." in login("technolog").get(f"/positions/{pid}").text
