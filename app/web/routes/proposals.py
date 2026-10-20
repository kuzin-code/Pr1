"""История коммерческих предложений: список с поиском, просмотр сохранённого КП, файл Word."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.services import proposal_history
from app.services.actor import ServiceError
from app.web.deps import current_actor, flash, get_db, redirect
from app.web.templating import render

router = APIRouter()
WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def storage(request: Request) -> Path:
    return Path(request.app.state.settings.upload_dir) / "proposals"


@router.get("/proposals")
def proposals_list(request: Request, q: str = "", db: Session = Depends(get_db)):
    actor = current_actor(request)
    rows = proposal_history.list_proposals(db, actor, q)
    return render(request, "proposals.html", rows=rows, q=q)


@router.get("/proposals/{proposal_id}")
def proposal_page(request: Request, proposal_id: int, new: int = 0, db: Session = Depends(get_db)):
    actor = current_actor(request)
    try:
        row = proposal_history.get_proposal(db, actor, proposal_id)
    except ServiceError as err:
        flash(request, str(err), "error")
        return redirect("/proposals")
    return render(request, "proposal.html", p=proposal_history.proposal_of(row), row=row, new=bool(new))


@router.get("/proposals/{proposal_id}/file")
def proposal_file(request: Request, proposal_id: int, db: Session = Depends(get_db)):
    row = proposal_history.get_proposal(db, current_actor(request), proposal_id)
    if not row.file_path or not Path(row.file_path).is_file():
        flash(request, "Файл КП не найден", "error")
        return redirect(f"/proposals/{proposal_id}")
    name = re.sub(r'[\\/:*?"<>|]+', "-", f"{row.number} {row.customer}".strip())
    return FileResponse(row.file_path, filename=f"{name}.docx", media_type=WORD)
