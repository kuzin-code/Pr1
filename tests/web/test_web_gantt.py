"""Веб: загрузка станков с ЧПУ — диаграмма Ганта по умолчанию, таблицы по дням и неделям."""
from tests.web.test_web_operations import approve


def test_gantt_is_default_view_with_bars_and_legend(login):
    pid = approve(login)
    page = login("head").get("/plan").text
    assert 'class="gantt"' in page
    assert "Haas VF-2" in page and "Haas ST-20Y" in page  # строки всех станков с ЧПУ
    assert f'href="/positions/{pid}"' in page and "×10" in page  # полоса позиции, количество
    assert "утверждена" in page and "предварительно" in page and "выполнена" in page  # легенда
    assert "gantt-bar approved" in page


def test_management_sees_gantt_without_links(login):
    approve(login)
    page = login("management").get("/plan").text
    assert 'class="gantt"' in page and "gantt-bar" in page
    assert 'href="/positions/' not in page


def test_table_views_still_available(login):
    approve(login)
    page = login("master").get("/plan?view=day").text
    assert 'class="gantt"' not in page and "%" in page
    assert "Неделя" in login("master").get("/plan?view=week").text or "-Н" in login("master").get("/plan?view=week").text


def test_bar_status_follows_operations(login):
    import re
    approve(login)
    master = login("master")
    for i in range(2):  # резка и наладка выполнены — станок в работе
        op = re.findall(r'action="/operations/(\d+)/start"', master.get("/work").text)[0]
        master.post(f"/operations/{op}/start", data={"at": f"2026-10-12T0{8 + i}:00"})
        master.post(f"/operations/{op}/finish", data={"at": f"2026-10-12T0{8 + i}:30"})
    assert "gantt-bar in_progress" in login("head").get("/plan").text
