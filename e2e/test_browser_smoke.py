"""Фронтенд: страницы под каждой ролью в настоящем браузере (Chromium, Playwright) — на ПК и телефоне.

Ловит ошибки JavaScript и консоли, ответы сервера 5xx и прокрутку страницы вбок на телефоне.
Запуск (в CI — GitHub Actions): pip install playwright && playwright install chromium && pytest e2e
Скриншоты — e2e/screens/ (в CI — артефакт сборки)."""
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.web.app import create_app  # noqa: E402
from tests.web.test_web_operations import approve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCREENS = Path(__file__).parent / "screens"
VIEWPORTS = {"pc": (1280, 900), "phone": (390, 844)}

# Роль → страницы (позиция и заказ подставляются после подготовки данных)
PAGES = {
    "technolog": ["/dashboard", "/orders", "/orders/new", "/order", "/position", "/quick", "/journal", "/reference",
                  "/history", "/proposals", "/plan"],
    "head": ["/dashboard", "/plan", "/plan?view=day", "/plan?view=week", "/plan/shift?machine=VF2", "/order",
             "/position", "/proposals", "/notifications"],
    "master": ["/dashboard", "/plan", "/work", "/position", "/notifications"],
    "operator": ["/work", "/notifications"],
    "zagotovitel": ["/work", "/notifications"],  # заготовка первой строкой на карточке резки
    "manager": ["/orders", "/orders/new", "/quick", "/order", "/proposals"],
    "management": ["/dashboard", "/plan"],
    "it": ["/users", "/reference", "/account"],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    settings = Settings(db_url=f"sqlite:///{(tmp / 'app.db').as_posix()}", upload_dir=tmp / "uploads",
                        seed_dir=ROOT / "data" / "seed", secret_key="e2e-secret", backups_dir=tmp / "backups",
                        drawings_dir=tmp / "КД")
    app = create_app(settings)
    clients = {}

    def login(username):
        if username not in clients:
            client = TestClient(app, follow_redirects=False)
            assert client.post("/login", data={"username": username, "password": "demo"}).status_code == 303
            clients[username] = client
        return clients[username]

    pid = approve(login)  # заказ с утверждённой позицией — чтобы на страницах были данные
    oid = int(re.search(r'href="/orders/(\d+)"', login("head").get(f"/positions/{pid}").text).group(1))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield {"base": f"http://127.0.0.1:{port}", "pid": pid, "oid": oid}
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        # Локально без скачивания Chromium: E2E_BROWSER_CHANNEL=msedge (установленный Edge)
        b = p.chromium.launch(channel=os.environ.get("E2E_BROWSER_CHANNEL") or None)
        yield b
        b.close()


def _url(path, server):
    return {"/order": f"/orders/{server['oid']}", "/position": f"/positions/{server['pid']}"}.get(path, path)


@pytest.mark.parametrize("device", list(VIEWPORTS))
@pytest.mark.parametrize("role", list(PAGES))
def test_pages_without_errors(server, browser, role, device):
    width, height = VIEWPORTS[device]
    context = browser.new_context(viewport={"width": width, "height": height}, locale="ru-RU")
    page = context.new_page()
    problems = []
    # «Failed to load resource» в консоли не пишем — тот же ответ ниже записывается с адресом
    page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
            and problems.append(f"консоль: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"JavaScript: {e}"))
    page.on("response", lambda r: r.status >= 400 and r.request.resource_type != "document"
            and problems.append(f"ресурс {r.status}: {r.url}"))
    page.on("response", lambda r: r.status >= 500 and r.request.resource_type == "document"
            and problems.append(f"сервер {r.status}: {r.url}"))

    page.goto(server["base"] + "/login")
    page.fill("input[name=username]", role)
    page.fill("input[name=password]", "demo")
    page.locator("form button, form input[type=submit]").first.click()
    page.wait_for_load_state("networkidle")

    SCREENS.mkdir(exist_ok=True)
    for path in PAGES[role]:
        url = _url(path, server)
        before = len(problems)
        resp = page.goto(server["base"] + url)
        page.wait_for_load_state("networkidle")
        if resp is None or resp.status >= 400:
            problems.append(f"{url}: ответ {resp.status if resp else 'нет'}")
        if device == "phone":
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if overflow > 2:
                problems.append(f"{url}: на телефоне страница прокручивается вбок на {overflow} px")
        name = url.strip("/").replace("/", "_").replace("?", "_").replace("=", "-") or "root"
        page.screenshot(path=str(SCREENS / f"{role}_{device}_{name}.png"), full_page=True)
        for i in range(before, len(problems)):
            if not problems[i].startswith(url):
                problems[i] = f"{url}: {problems[i]}"
    context.close()
    assert not problems, "\n".join(problems)
