"""Обслуживание приложения из командной строки.

    python manage.py status                         — сводка по базе
    python manage.py backup                         — резервная копия базы сейчас
    python manage.py reset-orders [--drop-demo-journal] [--yes]
                                                    — очистить заказы перед запуском в работу
                                                      (справочники, станки, пользователи, ставки и
                                                      журнал калибровки сохраняются; копия — заранее)
"""
import argparse
import sys

from app.config import Settings
from app.services import maintenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Обслуживание АС «Планирование загрузки и расчёт стоимости»")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="сводка по базе")
    sub.add_parser("backup", help="резервная копия базы")
    reset = sub.add_parser("reset-orders", help="очистить заказы перед запуском в работу")
    reset.add_argument("--drop-demo-journal", action="store_true", help="убрать демо-детали DEMO-01…09 из журнала")
    reset.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    args = parser.parse_args(argv)

    settings = Settings()
    db = settings.db_path
    if db is None or not db.exists():
        print(f"База не найдена: {settings.db_url}")
        return 1
    if args.command == "status":
        for table, count in maintenance.status(db).items():
            print(f"{table:12s} {count}")
    elif args.command == "backup":
        print(f"Резервная копия: {maintenance.backup_database(db, settings.backups_dir)}")
    else:
        print("Будут удалены все заказы, позиции, операции, факты и уведомления.")
        print("Справочники, станки, пользователи, ставки и журнал калибровки сохранятся.")
        if args.drop_demo_journal:
            print("Демо-детали DEMO-01…DEMO-09 будут удалены из журнала.")
        if not args.yes and input("Продолжить? Введите «да»: ").strip().lower() != "да":
            print("Отменено")
            return 1
        report = maintenance.reset_orders(db, settings.backups_dir, settings.upload_dir, args.drop_demo_journal)
        print(f"Удалено заказов: {report['orders']}. Копия базы до очистки: {report['backup']}")
        if report.get("uploads_moved_to"):
            print(f"Файлы заказов перенесены в: {report['uploads_moved_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
