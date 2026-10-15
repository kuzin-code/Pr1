"""Обновление схемы существующей базы: новые столбцы добавляются без потери данных."""
import sqlite3

from app.storage.db import init_db, make_engine
from app.storage.orm import JournalRow, MachineRow


def test_missing_columns_added_to_existing_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE machines (id INTEGER PRIMARY KEY, code VARCHAR(32) UNIQUE, model VARCHAR(128), "
                "kind VARCHAR(128), is_cnc BOOLEAN, work_zone JSON, technologies JSON, controller VARCHAR(128), "
                "accuracy VARCHAR(128), power VARCHAR(128), hours_per_day FLOAT, maintenance JSON, note TEXT, "
                "busy_until DATETIME)")
    con.execute("INSERT INTO machines (code, model, kind, is_cnc, technologies, hours_per_day, maintenance, note) "
                "VALUES ('VF2', 'Haas VF-2', 'фрезерный', 1, '[\"milling\"]', 6, '[]', '')")
    con.commit()
    con.close()

    factory = init_db(make_engine(f"sqlite:///{path}"))
    with factory() as s:
        row = s.query(MachineRow).one()
        assert row.model == "Haas VF-2"
        assert row.spindle_rpm is None
        row.spindle_rpm = 8100
        s.commit()
        assert s.query(JournalRow).count() == 0
