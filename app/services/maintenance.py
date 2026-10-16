"""Обслуживание базы: ежедневные резервные копии, очистка заказов перед запуском в работу.

Журнал калибровки — накопленный опыт участка (НФТ-06): резервная копия делается раз в сутки
и перед любой очисткой; хранятся последние KEEP копий.
"""
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

KEEP = 30
PREFIX = "app-"
STAMP = "%Y-%m-%d_%H%M"


def _stamp_of(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem[len(PREFIX):], STAMP)
    except ValueError:
        return None


def backup_database(db_path: Path | str, backups_dir: Path | str, keep: int = KEEP,
                    now: datetime | None = None) -> Path:
    """Согласованная копия SQLite (online backup API) — сервер может работать в это время."""
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = backups_dir / f"{PREFIX}{(now or datetime.now()):{STAMP}}.db"
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    copies = sorted((p for p in backups_dir.glob(f"{PREFIX}*.db") if _stamp_of(p)), key=_stamp_of)
    for old in copies[:-keep] if keep else []:
        old.unlink(missing_ok=True)
    return target


def backup_due(backups_dir: Path | str, now: datetime | None = None) -> bool:
    """Копия нужна, если последней больше суток."""
    stamps = [s for s in (_stamp_of(p) for p in Path(backups_dir).glob(f"{PREFIX}*.db")) if s]
    return not stamps or (now or datetime.now()) - max(stamps) >= timedelta(hours=24)


def _rescue_journal_files(db_path: Path | str, uploads_dir: Path) -> None:
    """Файлы деталей журнала, внесённых из позиций, лежат в папках заказов — переносятся в папку журнала."""
    with closing(sqlite3.connect(db_path)) as con, con:
        for row_id, *paths in con.execute("select id, step_path, pdf_path from journal").fetchall():
            for column, value in zip(("step_path", "pdf_path"), paths):
                if not value or "order_" not in Path(value).parent.name or not Path(value).exists():
                    continue
                target = uploads_dir / "journal" / f"entry_{row_id}" / Path(value).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(value, target)
                con.execute(f"update journal set {column} = ? where id = ?", (str(target), row_id))


def reset_orders(db_path: Path | str, backups_dir: Path | str, uploads_dir: Path | str,
                 drop_demo_journal: bool = False) -> dict:
    """Убирает заказы, позиции, операции, факты и уведомления; справочники, станки, пользователи,
    ставки и журнал калибровки сохраняются (замеры из удалённых позиций — убираются)."""
    backup = backup_database(db_path, backups_dir, keep=0)
    report = {"backup": backup}
    _rescue_journal_files(db_path, Path(uploads_dir))
    with closing(sqlite3.connect(db_path)) as con, con:
        report["orders"] = con.execute("select count(*) from orders").fetchone()[0]
        for table in ("operations", "facts", "notifications", "positions", "orders"):
            con.execute(f"delete from {table}")
        con.execute("delete from history where entity in ('position', 'order')")
        con.execute("update machines set busy_until = null")
        for row_id, measurements in con.execute("select id, measurements from journal").fetchall():
            kept = [m for m in json.loads(measurements or "[]") if m.get("position_id") is None]
            con.execute("update journal set measurements = ? where id = ?", (json.dumps(kept, ensure_ascii=False), row_id))
        if drop_demo_journal:
            report["demo_journal"] = con.execute("delete from journal where part_code like 'DEMO-%'").rowcount
        con.execute("update parameters set value = cast(value as integer) + 1 where key = 'journal_version'")
    moved = Path(backups_dir) / f"uploads-{datetime.now():{STAMP}}"
    for folder in Path(uploads_dir).glob("order_*"):
        moved.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), moved / folder.name)
    report["uploads_moved_to"] = moved if moved.exists() else None
    return report


def status(db_path: Path | str) -> dict:
    with closing(sqlite3.connect(db_path)) as con:
        return {table: con.execute(f"select count(*) from {table}").fetchone()[0]
                for table in ("orders", "positions", "operations", "journal", "machines", "users")}
