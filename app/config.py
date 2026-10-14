"""Настройки приложения (переменные окружения с префиксом APP_)."""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _secret_key() -> str:
    """Ключ подписи сессий: из окружения или из файла data/.secret_key (создаётся один раз)."""
    if os.environ.get("APP_SECRET_KEY"):
        return os.environ["APP_SECRET_KEY"]
    path = DATA_DIR / ".secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: os.environ.get(
        "APP_DB_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"))
    upload_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "APP_UPLOAD_DIR", DATA_DIR / "uploads")))
    seed_dir: Path = field(default_factory=lambda: Path(os.environ.get("APP_SEED_DIR", DATA_DIR / "seed")))
    secret_key: str = field(default_factory=_secret_key)
    demo_journal: bool = field(default_factory=lambda: os.environ.get("APP_DEMO_JOURNAL", "1") == "1")
