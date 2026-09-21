"""Panel de asesores (B-5): lista de leads de su zona y ficha completa de cada uno.

Auth por token estático en la URL (DT-5). Todo lo que no es del asesor del token responde 404 —
nunca 401/403, para que el código de estado no revele si un token o un lead existen.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import resolve_advisor
from app.repo import as_context, lead_detail, leads_for_advisor
from db.models import Advisor
from db.session import get_engine, session_scope

app = FastAPI(title="Panel de asesores")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

NOT_FOUND = HTTPException(status_code=404, detail="No encontrado")


def get_session() -> Session:
    with session_scope(get_engine()) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]


def current_advisor(advisor_token: str, session: DbSession) -> Advisor:
    advisor = resolve_advisor(session, advisor_token)
    if advisor is None:
        raise NOT_FOUND
    return advisor


CurrentAdvisor = Annotated[Advisor, Depends(current_advisor)]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/panel/{advisor_token}", response_class=HTMLResponse)
def leads_list(request: Request, advisor: CurrentAdvisor, session: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(request, "leads_list.html", {
        "advisor": advisor,
        "token": advisor.access_token,
        "leads": leads_for_advisor(session, advisor),
    })


@app.get("/panel/{advisor_token}/leads/{lead_id}", response_class=HTMLResponse)
def lead_card(request: Request, lead_id: uuid.UUID, advisor: CurrentAdvisor,
              session: DbSession) -> HTMLResponse:
    detail = lead_detail(session, advisor, lead_id)
    if detail is None:  # no existe, o es de otra zona: la misma respuesta para los dos
        raise NOT_FOUND
    return templates.TemplateResponse(request, "lead_detail.html", {
        "advisor": advisor, "token": advisor.access_token, **as_context(detail),
    })
