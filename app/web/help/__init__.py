"""Справка и кнопки помощи «?» (обучение персонала).

TOPICS — ключ → заголовок, 1–3 предложения, шаги, роли, экран. Ключ — якорь на странице /help
и аргумент макросов help_tip / help_link / help_empty (шаблон _help.html).
SECTIONS — разделы справки по ролям; раздел текущей роли показывается первым, за ним «Для всех».
"""
from dataclasses import dataclass

from app.domain.enums import Role
from app.web.help.topics_office import TOPICS as _OFFICE
from app.web.help.topics_shop import TOPICS as _SHOP

TOPICS: dict[str, dict] = {**_OFFICE, **_SHOP}

# Экраны — подпись у пункта справки (порядок — порядок пунктов внутри раздела).
SCREENS = {
    "start": "Вход и меню", "dashboard": "Рабочий экран", "work": "Мои операции", "shift": "Сменное задание",
    "intake": "Приём заказа", "order": "Карточка заказа", "position": "Карточка позиции", "quick": "Быстрый расчёт",
    "kp": "КП", "plan": "Загрузка станков", "journal": "Журнал калибровки",
    "notifications": "Уведомления", "admin": "Справочники и пользователи",
}


@dataclass(frozen=True)
class Section:
    code: str
    title: str
    lead: str
    icon: str  # имя значка в help.html
    roles: frozenset[Role]
    topics: tuple[str, ...]


def _for(*roles: str, common: bool = False) -> tuple[str, ...]:
    """Пункты для ролей в порядке экранов; общие (для всех ролей) — только в разделе «Для всех»."""
    everyone = {r.value for r in Role}
    keys = [k for k, t in TOPICS.items()
            if (set(t["roles"]) >= everyone) == common and (common or set(t["roles"]) & set(roles))]
    order = list(SCREENS)
    return tuple(sorted(keys, key=lambda k: order.index(TOPICS[k]["screen"])))


R = Role
SECTIONS: tuple[Section, ...] = (
    Section("manager", "Менеджер производства", "Приём заказа за минуту, быстрый расчёт цены и срока, КП.", "manager",
            frozenset({R.MANAGER}), _for("manager")),
    Section("technologist", "Технолог", "Проверка расчёта: данные чертежа, балл, аналог, маршрут, станок; журнал.",
            "technologist", frozenset({R.TECHNOLOGIST}), _for("technologist")),
    Section("head", "Начальник участка", "Утверждение позиций, загрузка станков, сроки и приоритет.", "head",
            frozenset({R.HEAD}), _for("head")),
    Section("master", "Мастер", "Заказы, загрузка станков, уведомления, операции за специалистов.", "master",
            frozenset({R.MASTER}), _for("master")),
    Section("shop", "Специалист у станка", "Оператор ЧПУ, заготовитель, слесарь, контролёр: «Начал» и «Закончил».",
            "shop", frozenset({R.OPERATOR, R.CUTTER, R.LOCKSMITH, R.INSPECTOR}),
            _for("operator", "cutter", "locksmith", "inspector")),
    Section("management", "Руководство", "Просмотр загрузки станков, сроков и отставаний.", "management",
            frozenset({R.MANAGEMENT}), _for("management")),
    Section("it", "IT", "Учётные записи, справочники, первичная загрузка журнала.", "it",
            frozenset({R.IT}), _for("it")),
    Section("common", "Для всех", "Вход, пароль, меню и сама справка.", "common",
            frozenset(), _for(common=True)),
)


def sections_for(role: Role | None) -> list[Section]:
    """Раздел роли первым, затем «Для всех», затем остальные в обычном порядке."""
    mine = [s for s in SECTIONS if role in s.roles]
    common = [s for s in SECTIONS if s.code == "common"]
    return mine + common + [s for s in SECTIONS if s not in mine and s not in common]


def help_topic(key: str) -> dict | None:
    """Пункт справки для макросов; неизвестный ключ — None (макрос ничего не выводит)."""
    topic = TOPICS.get(key)
    return None if topic is None else {"key": key, "screen_label": SCREENS[topic["screen"]], **topic}


def register(env) -> None:
    """Глобальная функция шаблонов для _help.html."""
    env.globals.update(help_topic=help_topic)
