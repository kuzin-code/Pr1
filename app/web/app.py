"""Фабрика веб-приложения."""
import logging
import mimetypes
import threading
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings
from app.domain.access import AccessDenied
from app.services.maintenance import backup_database, backup_due
from app.services.operation_service import ensure_all_operations
from app.services.seed import seed_reference
from app.storage.db import init_db, make_engine
from app.web.deps import MustChangePassword, NotLoggedIn, redirect
from app.web.routes import auth, dashboard, users, journal, machines, orders, plan, positions, reference, work
from app.web.templating import render


mimetypes.add_type("application/wasm", ".wasm")  # 3D-просмотр STEP (occt-import-js)


def start_daily_backup(settings: Settings, check_every: float = 3600) -> threading.Thread | None:
    """Резервная копия базы раз в сутки (проверка каждый час) — журнал калибровки не теряется."""
    db = settings.db_path
    if db is None:
        return None

    def loop():
        while True:
            try:
                if db.exists() and backup_due(settings.backups_dir):
                    backup_database(db, settings.backups_dir)
            except Exception:  # noqa: BLE001 — сбой копии не должен останавливать сервер
                logging.getLogger("backup").exception("Резервная копия не создана")
            time.sleep(check_every)

    thread = threading.Thread(target=loop, name="daily-backup", daemon=True)
    thread.start()
    return thread


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    session_factory = init_db(make_engine(settings.db_url))
    with session_factory() as session:
        seed_reference(session, settings.seed_dir, demo_journal=settings.demo_journal)
        ensure_all_operations(session)  # операции для позиций, утверждённых до учёта операций

    app = FastAPI(title="Планирование загрузки и расчёт стоимости", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    @app.exception_handler(NotLoggedIn)
    async def _not_logged_in(request: Request, exc: NotLoggedIn):
        return redirect("/login")

    @app.exception_handler(MustChangePassword)
    async def _must_change(request: Request, exc: MustChangePassword):
        return redirect("/account")

    @app.exception_handler(AccessDenied)
    async def _denied(request: Request, exc: AccessDenied):
        return render(request, "error.html", status_code=403, title="Нет доступа", message=str(exc))

    start_daily_backup(settings)
    for module in (auth, dashboard, users, orders, positions, plan, reference, journal, machines, work):
        app.include_router(module.router)
    return app
