"""Panel de asesores (B-5): lista de leads de su zona, ficha completa de cada uno y avisos (B-15).

Auth por token estático en la URL (DT-5). Todo lo que no es del asesor del token responde 404 —
nunca 401/403, para que el código de estado no revele si un token o un lead existen.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import resolve_advisor
from app.business import business_view, parse_period
from app.funnel import funnel_report
from app.inventory import LOCAL_TZ
from app.repo import (
    as_context,
    claim_lead,
    lead_detail,
    leads_for_advisor,
    notices_for_advisor,
    open_notice,
    unread_notices,
)
from app.sla import hot_wait_from_env, overdue
from db.logging_config import configure_json_logging
from db.models import Advisor
from db.session import get_engine, session_scope

configure_json_logging()

app = FastAPI(title="Panel de asesores")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

NOT_FOUND = HTTPException(status_code=404, detail="No encontrado")
REFRESH_SECONDS = 30  # los avisos no llegan solos al navegador: la página se recarga (B-15)
SLA_HOT_WAIT = hot_wait_from_env()  # B-12: un valor inválido falla al arrancar


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
    now = datetime.now(UTC)
    leads = leads_for_advisor(session, advisor)
    return templates.TemplateResponse(request, "leads_list.html", {
        "advisor": advisor,
        "token": advisor.access_token,
        "leads": leads,
        # B-12: los HOT sin tomar que pasaron el umbral, con cuánto llevan esperando.
        "overdue": {lead.id: wait for lead in leads if (wait := overdue(lead, now, SLA_HOT_WAIT))},
        "sla_minutes": int(SLA_HOT_WAIT.total_seconds() // 60),
        "unread": unread_notices(session, advisor),
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


@app.get("/panel/{advisor_token}/funnel", response_class=HTMLResponse)
def funnel(request: Request, advisor: CurrentAdvisor, session: DbSession) -> HTMLResponse:
    """Dashboard del funnel (B-9): global, lo ve cualquier asesor con token válido."""
    return templates.TemplateResponse(request, "funnel.html", {
        "advisor": advisor, "token": advisor.access_token, "report": funnel_report(session),
    })


@app.get("/panel/{advisor_token}/negocio", response_class=HTMLResponse)
def business(request: Request, advisor: CurrentAdvisor, session: DbSession, periodo: str | None = None,
             desde: str | None = None, hasta: str | None = None, demo: bool = False) -> HTMLResponse:
    """Inventario y negocio (N-8): global como el funnel (DT-41), solo agregados. `demo` suma la historia
    sintética de N-9."""
    now = datetime.now(UTC)
    period = parse_period(periodo, desde, hasta, today=now.astimezone(LOCAL_TZ).date())
    return templates.TemplateResponse(request, "negocio.html", {
        "advisor": advisor, "token": advisor.access_token,
        "view": business_view(session, period, now=now, include_synthetic=demo),
    })


@app.get("/panel/{advisor_token}/avisos", response_class=HTMLResponse)
def notices(request: Request, advisor: CurrentAdvisor, session: DbSession) -> HTMLResponse:
    """Avisos del asesor (B-15): leads calificados y traspasos. La página se recarga sola."""
    return templates.TemplateResponse(request, "avisos.html", {
        "advisor": advisor, "token": advisor.access_token,
        "notices": notices_for_advisor(session, advisor), "unread": unread_notices(session, advisor),
        "refresh_seconds": REFRESH_SECONDS,
    })


@app.get("/panel/{advisor_token}/avisos/{notice_id}")
def notice(notice_id: uuid.UUID, advisor: CurrentAdvisor, session: DbSession) -> RedirectResponse:
    """Abre el aviso: lo marca como leído y lleva a la ficha del lead."""
    lead_id = open_notice(session, advisor, notice_id)
    if lead_id is None:
        raise NOT_FOUND
    return RedirectResponse(url=f"/panel/{advisor.access_token}/leads/{lead_id}", status_code=303)


@app.post("/panel/{advisor_token}/leads/{lead_id}/gestionar")
def take_lead(lead_id: uuid.UUID, advisor: CurrentAdvisor, session: DbSession) -> RedirectResponse:
    """"Marcar en gestión" (B-6). Vuelve a la ficha; 303 para que el navegador haga GET."""
    if not claim_lead(session, advisor, lead_id):
        raise NOT_FOUND
    return RedirectResponse(url=f"/panel/{advisor.access_token}/leads/{lead_id}", status_code=303)
