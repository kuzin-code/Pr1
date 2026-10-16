"""Рабочий экран — главная страница технолога, мастера, начальника участка и руководства."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.domain.access import Action, can
from app.services import plan_service
from app.services.dashboard_service import build_dashboard
from app.web.deps import current_actor, get_db
from app.web.templating import render

router = APIRouter()

QUICK_ACTIONS = [
    (Action.UPLOAD_ORDER, "/orders/new", "Новый заказ"),
    (Action.VIEW_ORDERS, "/orders", "Заказы"),
    (Action.VIEW_PLAN, "/plan", "Загрузка станков"),
    (Action.DO_OPERATIONS, "/work", "Операции участка"),
    (Action.EDIT_JOURNAL, "/journal", "Журнал калибровки"),
    (Action.VIEW_REFERENCE, "/reference", "Справочники"),
]


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    actor = current_actor(request)
    if can(actor.role, Action.RECEIVE_NOTIFICATIONS):
        plan_service.sync_notifications(db)
    board = build_dashboard(db, actor)
    actions = [(url, label) for action, url, label in QUICK_ACTIONS if can(actor.role, action)]
    return render(request, "dashboard.html", board=board, actions=actions)
