"""Panel de asesores (B-5): lista de leads de su zona, ficha completa de cada uno y avisos (B-15).

Login con usuario y contraseña y dos roles (B-17, DT-72): el asesor ve su cola, su ficha y sus avisos; el
gerente, negocio y funnel. Lo que no corresponde a la sesión responde 404 — nunca 401/403, para que el
código de estado no revele si un lead o una vista existen (DT-5). Sin sesión, se va a `/login`. Las rutas
viejas con el token (`/panel/<token>/…`, las que manda el core) solo redirigen: el token ya no autentica.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import SESSION_MAX_AGE, authenticate, csrf_ok, new_csrf_token, session_secret_from_env
from app.business import business_view, parse_period
from app.funnel import funnel_report
from app.inventory import LOCAL_TZ
from app.landing import available_properties, bot_username_from_env, telegram_link
from app.repo import (
    Closing,
    ClosingRejected,
    as_context,
    claim_lead,
    close_deal,
    lead_detail,
    leads_for_advisor,
    notices_for_advisor,
    open_notice,
    unread_notices,
)
from app.sla import hot_wait_from_env, overdue
from db.labels import label
from db.logging_config import configure_json_logging
from db.models import Advisor, PanelUser
from db.session import get_engine, session_scope

configure_json_logging()

app = FastAPI(title="Agent panel")
# B-17: cookie firmada, HttpOnly (Starlette la marca siempre) y SameSite=Lax; vence 8 h después del login.
# Sin PANEL_SESSION_SECRET válido el panel no arranca, igual que con un SLA inválido.
app.add_middleware(SessionMiddleware, secret_key=session_secret_from_env(), session_cookie="panel_session",
                   max_age=SESSION_MAX_AGE, same_site="lax", https_only=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# El panel se lee en inglés (#202): los valores de la base (`venta`, `disponible`, `nuevo`...) pasan por `label`.
templates.env.globals["label"] = label

NOT_FOUND = HTTPException(status_code=404, detail="Not found")
CSRF_REJECTED = HTTPException(status_code=403, detail="Invalid form, reload the page")
LOGIN_ERROR = "Wrong username or password."  # el mismo para los dos casos: no revela qué usuarios existen
HOME = {"asesor": "/panel", "gerente": "/negocio"}
REFRESH_SECONDS = 30  # los avisos no llegan solos al navegador: la página se recarga (B-15)
SLA_HOT_WAIT = hot_wait_from_env()  # B-12: un valor inválido falla al arrancar


def get_session() -> Session:
    with session_scope(get_engine()) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]


class LoginRequired(Exception):
    """Ruta protegida sin sesión válida (no hay, venció o su usuario ya no existe): se redirige a /login."""


@app.exception_handler(LoginRequired)
def _to_login(request: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def current_user(request: Request, session: DbSession) -> PanelUser:
    uid = request.session.get("uid")
    user = session.get(PanelUser, uid) if isinstance(uid, int) else None
    if user is None:
        raise LoginRequired
    request.state.user = user
    return user


CurrentUser = Annotated[PanelUser, Depends(current_user)]


def current_advisor(user: CurrentUser, session: DbSession) -> Advisor:
    """El asesor de la sesión. Un gerente que pide la cola o una ficha recibe el 404 de siempre."""
    advisor = session.get(Advisor, user.advisor_id) if user.role == "asesor" and user.advisor_id else None
    if advisor is None:
        raise NOT_FOUND
    return advisor


CurrentAdvisor = Annotated[Advisor, Depends(current_advisor)]


def current_manager(user: CurrentUser) -> PanelUser:
    """El gerente de la sesión. Un asesor que pide negocio o funnel recibe el 404 de siempre."""
    if user.role != "gerente":
        raise NOT_FOUND
    return user


CurrentManager = Annotated[PanelUser, Depends(current_manager)]


def check_csrf(request: Request, csrf_token: Annotated[str, Form()] = "") -> None:
    """Los POST del panel llevan el token CSRF de la sesión; sin él, o con otro, no se hace nada."""
    if not csrf_ok(request.session, csrf_token):
        raise CSRF_REJECTED


Csrf = Annotated[None, Depends(check_csrf)]


def _page(request: Request, name: str, context: dict, *, status_code: int = 200) -> HTMLResponse:
    """La página con lo que necesita la cabecera: quién inició sesión y el token CSRF de sus formularios."""
    return templates.TemplateResponse(request, name, {
        "user": request.state.user, "csrf_token": request.session.get("csrf", ""), **context,
    }, status_code=status_code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/properties", response_class=HTMLResponse)
def public_properties(request: Request, session: DbSession) -> HTMLResponse:
    """Landing pública (B-16): sin token, solo lectura. Con `TELEGRAM_BOT_USERNAME` cada tarjeta lleva su
    enlace de Telegram; sin él, la landing sale igual y avisa que el canal no está configurado."""
    bot = bot_username_from_env()
    listings = [
        {"property": prop, "telegram_url": telegram_link(bot, prop.id) if bot else None}
        for prop in available_properties(session)
    ]
    return templates.TemplateResponse(request, "landing.html", {
        "listings": listings, "telegram_configured": bot is not None,
    })


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None, "username": ""})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, session: DbSession, username: Annotated[str, Form()] = "",
          password: Annotated[str, Form()] = ""):
    """Crea la sesión y lleva al asesor a su cola y al gerente a negocio. La sesión anterior se descarta
    entera (también su token CSRF), para que nadie pueda fijarla antes del login."""
    user = authenticate(session, username, password)
    if user is None:
        return templates.TemplateResponse(request, "login.html", {"error": LOGIN_ERROR, "username": username})
    request.session.clear()
    request.session.update({"uid": user.id, "csrf": new_csrf_token()})
    return RedirectResponse(url=HOME[user.role], status_code=303)


@app.post("/logout")
def logout(request: Request, user: CurrentUser, _: Csrf) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/panel", response_class=HTMLResponse)
def leads_list(request: Request, advisor: CurrentAdvisor, session: DbSession) -> HTMLResponse:
    now = datetime.now(UTC)
    leads = leads_for_advisor(session, advisor)
    return _page(request, "leads_list.html", {
        "advisor": advisor,
        "leads": leads,
        # B-12: los HOT sin tomar que pasaron el umbral, con cuánto llevan esperando.
        "overdue": {lead.id: wait for lead in leads if (wait := overdue(lead, now, SLA_HOT_WAIT))},
        "sla_minutes": int(SLA_HOT_WAIT.total_seconds() // 60),
        "unread": unread_notices(session, advisor),
    })


@app.get("/panel/leads/{lead_id}", response_class=HTMLResponse)
def lead_card(request: Request, lead_id: uuid.UUID, advisor: CurrentAdvisor,
              session: DbSession) -> HTMLResponse:
    return _lead_page(request, session, advisor, lead_id,
                      gestionado=request.query_params.get("gestionado") == "1")


def _lead_page(request: Request, session: Session, advisor: Advisor, lead_id: uuid.UUID, *,
               closing: Closing | None = None, close_error: str | None = None,
               gestionado: bool = False) -> HTMLResponse:
    detail = lead_detail(session, advisor, lead_id)
    if detail is None:  # no existe, o es de otra zona: la misma respuesta para los dos
        raise NOT_FOUND
    return _page(request, "lead_detail.html", {
        "advisor": advisor, **as_context(detail),
        "today": datetime.now(UTC).astimezone(LOCAL_TZ).date().isoformat(),
        "closing": closing, "close_error": close_error, "gestionado": gestionado,
        "unread": unread_notices(session, advisor),
    }, status_code=422 if close_error else 200)


@app.get("/panel/funnel", response_class=HTMLResponse)
def funnel(request: Request, manager: CurrentManager, session: DbSession) -> HTMLResponse:
    """Dashboard del funnel (B-9): global, solo para el gerente (B-17)."""
    return _page(request, "funnel.html", {"report": funnel_report(session)})


@app.get("/negocio", response_class=HTMLResponse)
def business(request: Request, manager: CurrentManager, session: DbSession, periodo: str | None = None,
             desde: str | None = None, hasta: str | None = None, demo: bool = False) -> HTMLResponse:
    """Inventario y negocio (N-8): global como el funnel (DT-41), solo agregados y solo para el gerente
    (B-17). `demo` suma la historia sintética de N-9."""
    now = datetime.now(UTC)
    period = parse_period(periodo, desde, hasta, today=now.astimezone(LOCAL_TZ).date())
    return _page(request, "negocio.html", {
        "view": business_view(session, period, now=now, include_synthetic=demo),
    })


@app.get("/panel/avisos", response_class=HTMLResponse)
def notices(request: Request, advisor: CurrentAdvisor, session: DbSession) -> HTMLResponse:
    """Avisos del asesor (B-15): leads calificados y traspasos. La página se recarga sola."""
    return _page(request, "avisos.html", {
        "advisor": advisor,
        "notices": notices_for_advisor(session, advisor), "unread": unread_notices(session, advisor),
        "refresh_seconds": REFRESH_SECONDS,
    })


@app.get("/panel/avisos/{notice_id}")
def notice(notice_id: uuid.UUID, advisor: CurrentAdvisor, session: DbSession) -> RedirectResponse:
    """Abre el aviso: lo marca como leído y lleva a la ficha del lead."""
    lead_id = open_notice(session, advisor, notice_id)
    if lead_id is None:
        raise NOT_FOUND
    return RedirectResponse(url=f"/panel/leads/{lead_id}", status_code=303)


@app.post("/panel/leads/{lead_id}/gestionar")
def take_lead(lead_id: uuid.UUID, advisor: CurrentAdvisor, _: Csrf, session: DbSession) -> RedirectResponse:
    """"Marcar en gestión" (B-6). Vuelve a la ficha; 303 para que el navegador haga GET. El
    `gestionado=1` hace que la ficha muestre la confirmación (una vez)."""
    if not claim_lead(session, advisor, lead_id):
        raise NOT_FOUND
    return RedirectResponse(url=f"/panel/leads/{lead_id}?gestionado=1", status_code=303)


@app.post("/panel/leads/{lead_id}/cerrar", response_class=HTMLResponse)
def close_operation(request: Request, lead_id: uuid.UUID, advisor: CurrentAdvisor, _: Csrf, session: DbSession,
                    property_id: Annotated[str, Form()] = "", kind: Annotated[str, Form()] = "",
                    price: Annotated[str, Form()] = "", closed_on: Annotated[str, Form()] = ""):
    """"Cerrar operación" (N-3). Vuelve a la ficha con un 303, como "Marcar en gestión"; si el formulario
    no es válido, la ficha muestra el error y no se escribió nada."""
    closing = Closing(property_id, kind, price, closed_on)
    try:
        closed = close_deal(session, advisor, lead_id, closing, now=datetime.now(UTC))
    except ClosingRejected as error:
        session.rollback()  # nada de un cierre rechazado llega al commit del request
        return _lead_page(request, session, advisor, lead_id, closing=closing, close_error=str(error))
    if not closed:
        raise NOT_FOUND
    return RedirectResponse(url=f"/panel/leads/{lead_id}", status_code=303)


# --- Rutas viejas con el token (DT-5) ------------------------------------------------------------------
# El core sigue mandando enlaces `/panel/<token>/…` (avisos al asesor, reporte semanal). Redirigen a la ruta
# nueva sin mirar el token, así no revelan si existe ni autentican: sin sesión, la ruta nueva pide login.
# Van al final para que `/panel/avisos` o `/panel/funnel` no caigan en `/panel/{advisor_token}`.

def _moved(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


@app.get("/panel/{advisor_token}")
def old_leads_list(advisor_token: str) -> RedirectResponse:
    return _moved("/panel")


@app.get("/panel/{advisor_token}/leads/{lead_id}")
def old_lead_card(advisor_token: str, lead_id: uuid.UUID) -> RedirectResponse:
    return _moved(f"/panel/leads/{lead_id}")


@app.get("/panel/{advisor_token}/avisos")
def old_notices(advisor_token: str) -> RedirectResponse:
    return _moved("/panel/avisos")


@app.get("/panel/{advisor_token}/avisos/{notice_id}")
def old_notice(advisor_token: str, notice_id: uuid.UUID) -> RedirectResponse:
    return _moved(f"/panel/avisos/{notice_id}")


@app.get("/panel/{advisor_token}/funnel")
def old_funnel(advisor_token: str) -> RedirectResponse:
    return _moved("/panel/funnel")


@app.get("/panel/{advisor_token}/negocio")
def old_business(request: Request, advisor_token: str) -> RedirectResponse:
    query = request.url.query
    return _moved("/negocio" + (f"?{query}" if query else ""))
