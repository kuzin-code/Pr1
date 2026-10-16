"""Обслуживание: резервные копии базы, очистка заказов перед запуском в работу."""
import sqlite3
from datetime import datetime, timedelta

from app.services import maintenance
from app.services import reference_service as ref
from app.services.seed import seed_reference
from app.storage.db import init_db, make_engine
from app.storage.orm import FactRow, OperationRow, OrderRow, PositionRow, User
from tests.services.conftest import MON, SEED_DIR, part_files
from tests.services.test_operation_flow import CUTTER, at


def file_db(tmp_path):
    path = tmp_path / "app.db"
    factory = init_db(make_engine(f"sqlite:///{path.as_posix()}"))
    with factory() as s:
        seed_reference(s, SEED_DIR)
    return path, factory


def test_backup_copies_database_and_keeps_last_n(tmp_path):
    path, _ = file_db(tmp_path)
    backups = tmp_path / "backups"
    for i in range(4):
        maintenance.backup_database(path, backups, keep=3, now=datetime(2026, 10, 10 + i, 3, 0))
    files = sorted(p.name for p in backups.glob("*.db"))
    assert files == ["app-2026-10-11_0300.db", "app-2026-10-12_0300.db", "app-2026-10-13_0300.db"]
    with sqlite3.connect(backups / files[-1]) as con:
        assert con.execute("select count(*) from machines").fetchone()[0] == 7


def test_backup_due_once_a_day(tmp_path):
    backups = tmp_path / "b"
    now = datetime(2026, 10, 16, 9, 0)
    assert maintenance.backup_due(backups, now)
    path, _ = file_db(tmp_path)
    maintenance.backup_database(path, backups, now=now)
    assert not maintenance.backup_due(backups, now + timedelta(hours=23))
    assert maintenance.backup_due(backups, now + timedelta(hours=25))


def test_reset_orders_keeps_reference_and_journal(tmp_path):
    from app.services import approval_service as approval
    from app.services import operation_service as ops
    from app.services import tech_service as tech
    from app.services.order_service import create_order
    from tests.services.conftest import HEAD, MASTER, TECH

    path, factory = file_db(tmp_path)
    with factory() as s:
        order = create_order(s, MASTER, MON, part_files(), tmp_path / "uploads")
        pos = order.positions[0]
        tech.submit(s, TECH, pos.id)
        approval.approve(s, HEAD, pos.id)
        row = ops.position_operations(s, pos.id)[0]
        ops.start(s, CUTTER, row.id, at(12, 8))
        ref.add_journal_measurement(s, "DEMO-01", __import__("app.domain.enums", fromlist=["StageGroup"]).StageGroup.MACHINE, 1, 1.2)
        s.commit()
    report = maintenance.reset_orders(path, tmp_path / "backups", tmp_path / "uploads", drop_demo_journal=True)
    assert report["orders"] == 1 and report["backup"].exists()
    with factory() as s:
        assert s.query(OrderRow).count() == s.query(PositionRow).count() == 0
        assert s.query(OperationRow).count() == s.query(FactRow).count() == 0
        assert s.query(User).count() == 9 and len(ref.list_machines(s)) == 7
        assert [e.part_code for e in ref.journal_entries(s)] == []  # демо-детали убраны
    assert not any((tmp_path / "uploads").glob("order_*"))


def test_reset_keeps_journal_files_that_lived_in_order_folders(tmp_path):
    from app.services import journal_service as js
    from app.services.order_service import create_order
    from app.services.file_check import check_entry_files
    from tests.services.conftest import MASTER, TECH

    path, factory = file_db(tmp_path)
    with factory() as s:
        pos = create_order(s, MASTER, MON, part_files("Кронштейн-К1"), tmp_path / "uploads").positions[0]
        js.add_to_journal(s, TECH, pos.id)
    maintenance.reset_orders(path, tmp_path / "backups", tmp_path / "uploads")
    with factory() as s:
        row = js.entry_row_by_code(s, "Кронштейн-К1")
        assert "journal" in row.step_path and all(st.ok for st in check_entry_files(row))


def test_daily_backup_thread_and_cli(tmp_path, monkeypatch, capsys):
    import time
    from app.config import Settings
    from app.web.app import start_daily_backup
    path, _ = file_db(tmp_path)
    settings = Settings(db_url=f"sqlite:///{path.as_posix()}", backups_dir=tmp_path / "bk", secret_key="x")
    assert settings.db_path == path
    assert start_daily_backup(Settings(db_url="sqlite://", secret_key="x")) is None  # база в памяти
    start_daily_backup(settings, check_every=3600)
    for _ in range(50):
        if list((tmp_path / "bk").glob("app-*.db")):
            break
        time.sleep(0.05)
    assert list((tmp_path / "bk").glob("app-*.db"))

    import manage
    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("APP_BACKUPS_DIR", str(tmp_path / "bk2"))
    assert manage.main(["status"]) == 0 and "machines" in capsys.readouterr().out
    assert manage.main(["backup"]) == 0 and list((tmp_path / "bk2").glob("app-*.db"))
