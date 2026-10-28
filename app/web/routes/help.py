"""Справка для обучения персонала: разделы по ролям (текущая роль первой), поиск, якоря для «Как это сделать?»."""
from fastapi import APIRouter, Request

from app.web.deps import current_actor
from app.web.help import SCREENS, TOPICS, register, sections_for
from app.web.templating import render, templates

router = APIRouter()
register(templates.env)  # help_topic() для макросов _help.html на всех страницах


@router.get("/help")
def help_page(request: Request):
    actor = current_actor(request)
    return render(request, "help.html", sections=sections_for(actor.role), topics=TOPICS, screens=SCREENS)
