"""Справка и кнопки помощи «?»: страница /help, тексты, макросы, НФТ-01."""
import re
from pathlib import Path

import pytest

from app.domain.enums import Role
from app.web.help import SCREENS, SECTIONS, TOPICS, sections_for
from app.web.templating import templates

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "web" / "templates"
USERS = {"technolog": Role.TECHNOLOGIST, "master": Role.MASTER, "operator": Role.OPERATOR, "head": Role.HEAD,
         "it": Role.IT, "management": Role.MANAGEMENT, "zagotovitel": Role.CUTTER, "slesar": Role.LOCKSMITH,
         "kontroler": Role.INSPECTOR, "manager": Role.MANAGER}


@pytest.mark.parametrize("username", USERS)
def test_help_page_opens_for_every_role(login, username):
    resp = login(username).get("/help")
    assert resp.status_code == 200
    html = resp.text
    assert "Справка" in html and 'id="help-search"' in html
    # Раздел текущей роли — первый.
    first = re.search(r'<section class="help-sec[^"]*" id="(sec-[\w-]+)"', html).group(1)
    assert first == "sec-" + sections_for(USERS[username])[0].code


def test_help_requires_login(app):
    from fastapi.testclient import TestClient
    resp = TestClient(app, follow_redirects=False).get("/help")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_every_topic_has_anchor_on_help_page(login):
    html = login("master").get("/help").text
    for key in TOPICS:
        assert f'id="{key}"' in html, key


def test_help_in_menu(login):
    for username in ("manager", "operator", "it"):
        assert 'href="/help"' in login(username).get("/account").text


@pytest.mark.parametrize("key", TOPICS)
def test_every_topic_has_text(key):
    t = TOPICS[key]
    assert t["title"].strip() and t["text"].strip(), key
    assert 1 <= t["text"].count(".") + t["text"].count("!") <= 4, f"{key}: 1–3 предложения"
    assert t["screen"] in SCREENS, key
    assert t["roles"], key
    assert all(s.strip() for s in t.get("steps", [])), key
    assert len(t.get("steps", [])) <= 5, key
    assert re.fullmatch(r"[a-z]+\.[a-z_]+", key), key


def test_sections_cover_every_role_and_topic():
    covered = {r for s in SECTIONS for r in s.roles}
    assert covered == set(Role)
    in_sections = {k for s in SECTIONS for k in s.topics}
    assert in_sections == set(TOPICS)


def test_current_role_first_then_common():
    order = [s.code for s in sections_for(Role.OPERATOR)]
    assert order[0] == "shop" and order[1] == "common" and len(order) == len(SECTIONS)
    assert [s.code for s in sections_for(Role.IT)][0] == "it"


def _render(source: str) -> str:
    return templates.env.from_string('{% import "_help.html" as help %}' + source).render()


def test_help_tip_renders_hint_in_place():
    html = _render("{{ help.help_tip('work.reason') }}")
    assert '<details class="help-tip">' in html and "<summary" in html
    assert TOPICS["work.reason"]["title"] in html
    assert TOPICS["work.reason"]["text"] in html
    assert 'href="/help#work.reason"' in html


def test_help_link_and_empty_state():
    link = _render("{{ help.help_link('intake.files') }}")
    assert 'href="/help#intake.files"' in link and "Как это сделать?" in link
    empty = _render("{{ help.help_empty('work.queue', 'Операций в очереди нет') }}")
    assert "Операций в очереди нет" in empty and TOPICS["work.queue"]["steps"][0] in empty


def test_unknown_key_renders_nothing():
    assert _render("{{ help.help_tip('nope.nope') }}{{ help.help_link('nope.nope') }}").strip() == ""


def test_help_keys_used_in_templates_exist():
    used = set()
    for path in TEMPLATES.glob("*.html"):
        used |= set(re.findall(r"help_(?:tip|link|empty)\(\s*'([^']+)'", path.read_text(encoding="utf-8")))
    assert used - set(TOPICS) == set()


def test_help_has_no_external_links(login):
    html = login("head").get("/help").text
    assert not re.search(r"(src|href)=\"(https?:)?//", html)
    files = [TEMPLATES / "_help.html", TEMPLATES / "help.html", ROOT / "app" / "web" / "static" / "help.css",
             *(ROOT / "app" / "web" / "help").glob("*.py")]
    for path in files:
        assert not re.search(r"https?://|@import|url\(", path.read_text(encoding="utf-8")), path.name
