"""Пользователь, выполняющий действие, и общая ошибка сервисов."""
from dataclasses import dataclass

from app.domain.enums import Role


class ServiceError(ValueError):
    """Ошибка бизнес-правила, текст показывается пользователю."""


@dataclass(frozen=True)
class Actor:
    username: str
    role: Role
