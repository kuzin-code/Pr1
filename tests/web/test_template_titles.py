"""Шаблоны: в {% block title %} — только текст заголовка вкладки (без разметки и скриптов).

Ошибка слияния однажды скопировала в заголовок раздел «Часы сверх плана» и скрипт приоритета."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "web" / "templates"
TITLE = re.compile(r"\{% block title %\}(.*?)\{% endblock %\}", re.S)


def test_title_blocks_are_plain_text():
    bad = []
    for path in sorted(TEMPLATES.glob("*.html")):
        for title in TITLE.findall(path.read_text(encoding="utf-8")):
            if "\n" in title or re.search(r"<\w", title):
                bad.append(f"{path.name}: {title[:60]!r}")
    assert not bad, "Разметка в заголовке вкладки:\n" + "\n".join(bad)


def test_dashboard_title_with_overruns(login):
    page = login("head").get("/dashboard").text
    title = re.search(r"<title>(.*?)</title>", page, re.S).group(1)
    assert title.strip() == "Главная — АС"
