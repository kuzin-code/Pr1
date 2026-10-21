import os

# Ускоряет хеширование демо-паролей в тестах; число итераций хранится в самом хеше.
os.environ.setdefault("APP_PBKDF2_ITERATIONS", "1000")

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

# «Сейчас» для плана в тестах: раньше всех дат тестовых заказов — расчёты детерминированы.
# Тесты «план не в прошлом» передают now явно или подменяют clock сами.
TEST_NOW = datetime(2026, 1, 5, 9, 0)


@pytest.fixture(autouse=True)
def fixed_plan_clock(monkeypatch):
    from app.services import plan_service
    monkeypatch.setattr(plan_service, "clock", lambda: TEST_NOW)
