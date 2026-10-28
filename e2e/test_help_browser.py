"""Кнопки помощи в настоящем браузере (Playwright) — на ПК и телефоне.

Каждая видимая «?» раскрывается кликом (видна подсказка) и закрывается: на ПК — повторным нажатием «?»,
на телефоне — кнопкой «Закрыть». Каждая ссылка «Как это сделать?» и «Подробнее в справке» ведёт на
существующий пункт страницы /help (пункт подсвечивается).
Запуск локально: E2E_BROWSER_CHANNEL=msedge pytest e2e"""
import pytest

pytest.importorskip("playwright.sync_api")

from e2e import test_browser_smoke as smoke  # noqa: E402
from e2e.test_browser_smoke import PAGES, VIEWPORTS, _url  # noqa: E402

server, browser = smoke.server, smoke.browser  # фикстуры сервера с демо-данными и браузера — общие со smoke-тестом


def _login(page, server, role):
    page.goto(server["base"] + "/login")
    page.fill("input[name=username]", role)
    page.fill("input[name=password]", "demo")
    page.locator("form button").first.click()
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("device", list(VIEWPORTS))
@pytest.mark.parametrize("role", list(PAGES))
def test_help_tips_open_close_and_links_lead_to_help(server, browser, role, device):
    width, height = VIEWPORTS[device]
    context = browser.new_context(viewport={"width": width, "height": height}, locale="ru-RU")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"JavaScript: {e}"))
    _login(page, server, role)
    keys, opened = set(), 0
    for path in PAGES[role] + ["/help"]:
        url = _url(path, server)
        page.goto(server["base"] + url)
        page.wait_for_load_state("networkidle")
        keys |= set(page.eval_on_selector_all(
            "a.help-link, a.help-more", "els => els.map(a => decodeURIComponent(a.hash.slice(1)))"))
        tips = page.locator("details.help-tip")
        for i in range(tips.count()):
            tip = tips.nth(i)
            if not tip.is_visible():
                continue  # внутри свёрнутого блока (например, «Причина отклонения» до открытия)
            tip.locator("summary").click()
            assert tip.evaluate("d => d.open"), f"{url}: «?» №{i + 1} не раскрылась"
            assert tip.locator(".help-pop").is_visible(), f"{url}: подсказка №{i + 1} не видна"
            if device == "phone":
                tip.locator(".help-close").click()
            else:
                tip.locator("summary").click()
            assert not tip.evaluate("d => d.open"), f"{url}: «?» №{i + 1} не закрылась"
            opened += 1
    assert opened > 0, "на экранах роли нет ни одной «?»"
    page.goto(server["base"] + "/help")
    ids = set(page.eval_on_selector_all(".help-item[id]", "els => els.map(e => e.id)"))
    assert keys and keys <= ids, f"ссылки ведут на несуществующие пункты: {keys - ids}"
    for key in sorted(keys)[:3]:  # переход по якорю подсвечивает пункт
        page.goto(f"{server['base']}/help#{key}")
        assert page.locator(f'[id="{key}"]').evaluate("e => e.classList.contains('hit')")
    context.close()
    assert not errors, "\n".join(errors)
