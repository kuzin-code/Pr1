"""Разовая миграция 29.10.2026: второй станок — часть техпроцесса детали, а не отдельная запись журнала.

Время SMM из записи «XWS.65.A.1.P001040 Оправа зеркала (SMM)» переносится во второй станок основной записи,
запись «(SMM)» удаляется вместе с папкой файлов entry_<id> (там копия файлов основной записи — проверяется
по содержимому; если файлы отличаются, папка остаётся). Всё — в историю с причиной.

    python scripts/migrate_second_machine.py <база.db> <папка файлов журнала>
    python scripts/migrate_second_machine.py data/app.db data/uploads/journal

Сначала — на копии базы и копии папки файлов; к рабочей базе — после резервной копии в data/backups/.
Повторный запуск ничего не меняет.
"""
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.domain.enums import Role, Stage  # noqa: E402
from app.services.actor import Actor  # noqa: E402
from app.services.history_service import record  # noqa: E402
from app.services.reference_service import bump_journal_version  # noqa: E402
from app.storage.orm import JournalRow, MachineRow  # noqa: E402

MAIN = "XWS.65.A.1.P001040 Оправа зеркала"
SECOND = f"{MAIN} (SMM)"
REASON = "второй станок — часть техпроцесса детали (уточнение пользователя 29.10.2026)"
ACTOR = Actor("миграция", Role.TECHNOLOGIST)


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _same_files(folder: Path, main_folder: Path) -> bool:
    """Все файлы папки записи «(SMM)» есть в папке основной записи с тем же содержимым."""
    files = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
    return all(_digest(p) == _digest(main_folder / p.name) for p in files)


def migrate(session, uploads_dir: Path | str) -> list[str]:
    """Возвращает строки отчёта; изменения фиксируются одной транзакцией."""
    uploads_dir = Path(uploads_dir)
    main = session.scalar(select(JournalRow).where(JournalRow.part_code == MAIN))
    second = session.scalar(select(JournalRow).where(JournalRow.part_code == SECOND))
    if main is None:
        return [f"Нет записи «{MAIN}» — миграция не нужна"]
    if second is None:
        return [f"Записи «{SECOND}» нет; второй станок основной записи: {main.extra_machines or 'нет'}"]
    hours = {s["stage"]: s["hours"] for s in second.stages}
    machine = session.scalar(select(MachineRow).where(MachineRow.code == second.machine_code))
    techs = set(machine.technologies or []) if machine else set()
    technology = second.technology if second.technology in techs or not techs else sorted(techs)[0]
    extra = {"machine": second.machine_code, "technology": technology,
             "setup": float(hours.get(Stage.SETUP.value, 0.0)), "machining": float(hours.get(Stage.MACHINING.value, 0.0))}
    old = list(main.extra_machines or [])
    main.extra_machines = [e for e in old if e.get("machine") != extra["machine"]] + [extra]
    record(session, ACTOR, "journal", main.part_code, "extra_machine", old, main.extra_machines, REASON)

    folder = uploads_dir / f"entry_{second.id}"
    remove_folder = _same_files(folder, uploads_dir / f"entry_{main.id}")
    record(session, ACTOR, "journal", second.part_code, "delete", second.part_code, None, REASON)
    session.delete(second)
    bump_journal_version(session)
    session.commit()
    report = [f"Основная запись «{MAIN}»: второй станок {extra['machine']} — наладка {extra['setup']} ч/партия, "
              f"обработка {extra['machining']} ч/шт", f"Запись «{SECOND}» удалена"]
    if folder.is_dir() and remove_folder:
        shutil.rmtree(folder)
        report.append(f"Папка {folder} удалена (копия файлов основной записи)")
    elif folder.is_dir():
        report.append(f"Папка {folder} оставлена: файлы отличаются от файлов основной записи — проверьте вручную")
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    from app.storage.db import init_db, make_engine

    db = Path(argv[0])
    if not db.is_file():
        print(f"База не найдена: {db}")
        return 1
    engine = make_engine(f"sqlite:///{db.resolve().as_posix()}")
    try:
        with init_db(engine)() as session:  # init_db добавляет столбец journal.extra_machines
            for line in migrate(session, argv[1]):
                print(line)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
