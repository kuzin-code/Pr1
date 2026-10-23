"""Выгрузка заказов за период в 1С файлом (CSV или Excel)."""
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.services import export_service
from app.services.actor import ServiceError
from app.web.deps import current_actor, flash, get_db, redirect

router = APIRouter()


@router.get("/orders/export-1c")
def export_1c(request: Request, start: str = Query("", alias="from"), end: str = Query("", alias="to"),
              fmt: str = "csv", db: Session = Depends(get_db)):
    """Заказы, принятые за период (по умолчанию — с начала месяца по сегодня)."""
    actor = current_actor(request)
    today = date.today()
    try:
        first = date.fromisoformat(start) if start else today.replace(day=1)
        last = date.fromisoformat(end) if end else today
        name, kind, data = export_service.export_1c(db, actor, first, last, fmt)
    except ValueError as err:  # неверная дата, формат или период
        db.rollback()
        flash(request, str(err) if isinstance(err, ServiceError) else "Неверная дата периода выгрузки", "error")
        return redirect("/orders")
    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    return Response(data, media_type=kind, headers={"Content-Disposition": disposition})
