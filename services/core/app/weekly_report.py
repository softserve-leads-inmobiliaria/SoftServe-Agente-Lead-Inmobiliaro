"""Reporte semanal al gerente por Telegram (N-10): colocadas, estancadas y demanda sin cubrir.

Dos clases de *job* del *scheduler* (A-17, DT-44, DT-47):
- `weekly_report_plan`, uno solo pendiente a la vez (clave `semanal`). Cada semana encola un
  `weekly_report` por destinatario y se vuelve a programar para la semana siguiente. El primero lo
  programa `core` al arrancar, si hay destinatarios.
- `weekly_report`, uno por semana y destinatario (clave `<año>-W<semana>:<chat>`, con `once=True`). Si
  Telegram falla, el *scheduler* lo reintenta; una vez enviado, la misma semana no se vuelve a mandar.

Destinatarios: los chats de `WEEKLY_REPORT_CHAT_IDS`, separados por coma. Sin la variable no se programa
nada. Las cifras salen de `db/metrics/` (N-6), las mismas que ve el panel. Solo agregados (DT-41): ni
leads ni texto de conversaciones, solo propiedades y conteos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache

from sqlalchemy.orm import Session

from app import jobs
from db.labels import label
from db.metrics.inventory import (
    LOCAL_TZ,
    Placements,
    StagnantProperty,
    UnmetDemand,
    placements,
    stagnant,
    unmet_demand,
    week_of,
)

PLAN_KIND = "weekly_report_plan"
REPORT_KIND = "weekly_report"
PLAN_KEY = "semanal"

STAGNANT_MIN_DAYS = 60
STAGNANT_SHOWN = 5  # las más antiguas; el resto se cuenta
UNMET_SHOWN = 3
# La vista de inventario y negocio de N-8. El enlace no lleva el token: cada asesor usa el suyo.
BUSINESS_PATH = "/panel/<your token>/negocio"


@dataclass(frozen=True)
class ReportConfig:
    chat_ids: tuple[str, ...]
    weekday: int = 0  # 0 = lunes
    hour: int = 9  # hora local
    panel_url: str = "http://localhost:8080"
    include_synthetic: bool = False

    @classmethod
    def from_env(cls) -> ReportConfig:
        chats = tuple(c.strip() for c in os.environ.get("WEEKLY_REPORT_CHAT_IDS", "").split(",") if c.strip())
        weekday, hour = int(os.environ.get("WEEKLY_REPORT_WEEKDAY", "0")), int(os.environ.get("WEEKLY_REPORT_HOUR", "9"))
        if not (0 <= weekday <= 6 and 0 <= hour <= 23):
            raise ValueError("WEEKLY_REPORT_WEEKDAY va de 0 a 6 y WEEKLY_REPORT_HOUR de 0 a 23")
        return cls(chat_ids=chats, weekday=weekday, hour=hour,
                   panel_url=os.environ.get("PANEL_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
                   include_synthetic=os.environ.get("WEEKLY_REPORT_INCLUDE_SYNTHETIC", "0") == "1")


@lru_cache
def default_config() -> ReportConfig:
    return ReportConfig.from_env()


def next_run(after: datetime, config: ReportConfig) -> datetime:
    """El próximo día y hora de envío, en hora local, estrictamente después de `after`."""
    local = after.astimezone(LOCAL_TZ)
    day = local.date() + timedelta(days=(config.weekday - local.weekday()) % 7)
    candidate = datetime.combine(day, time(config.hour), tzinfo=LOCAL_TZ)
    return candidate if candidate > after else candidate + timedelta(days=7)


def reported_week(run_at: datetime) -> tuple[datetime, datetime]:
    """La última semana completa (lunes a lunes, hora local) antes de `run_at`."""
    current_start, _ = week_of(run_at.astimezone(LOCAL_TZ).date())
    return current_start - timedelta(days=7), current_start


def week_label(start: datetime) -> str:
    year, week, _ = start.date().isocalendar()
    return f"{year}-W{week:02d}"


def schedule(session: Session, now: datetime, config: ReportConfig | None = None) -> bool:
    """Programa el próximo plan semanal si hay destinatarios. Idempotente: si ya hay uno pendiente, no hace nada."""
    config = config or default_config()
    if not config.chat_ids:
        return False
    return jobs.enqueue(session, PLAN_KIND, PLAN_KEY, next_run(now, config))


def plan(session: Session, run_at: datetime, now: datetime, config: ReportConfig | None = None) -> int:
    """Encola el reporte de la semana para cada destinatario y programa el plan de la semana siguiente."""
    config = config or default_config()
    start, end = reported_week(run_at)
    queued = sum(jobs.enqueue(session, REPORT_KIND, f"{week_label(start)}:{chat_id}", now, once=True,
                              payload={"chat_id": chat_id, "start": start.isoformat(), "end": end.isoformat()})
                 for chat_id in config.chat_ids)
    schedule(session, max(now, run_at), config)
    return queued


# --- el mensaje --------------------------------------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(moment: datetime) -> str:
    """`Oct 5`: el mes en inglés sin depender del *locale* del sistema."""
    return f"{_MONTHS[moment.month - 1]} {moment.day}"


def _money(amount: Decimal) -> str:
    return f"${amount:,.0f}"


def _count(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _unmet_line(item: UnmetDemand) -> str:
    """Una línea de la demanda no atendida: "- apartment for rent in Laureles, 2 bedrooms: 3 searches"."""
    operation = label("property_type", item.type) if item.type else None
    what = " ".join(part for part in (label("property_kind", item.kind) if item.kind else None, operation) if part)
    what = what or "any type"
    where = f"in {item.zone}" if item.zone else "in any area"
    rooms = f", {_count(item.bedrooms, 'bedroom', 'bedrooms')}" if item.bedrooms is not None else ""
    return f"- {what} {where}{rooms}: {_count(item.searches, 'search', 'searches')}"


def report_text(start: datetime, end: datetime, placed: Placements, stuck: list[StagnantProperty],
                unmet: list[UnmetDemand], panel_url: str) -> str:
    last_day = (end - timedelta(days=1)).astimezone(LOCAL_TZ)
    lines = [f"Weekly report: {_day(start.astimezone(LOCAL_TZ))} to {_day(last_day)}, {last_day.year}", ""]

    lines.append(f"Placed: {placed.deals}" + (f" ({_money(placed.amount)})" if placed.deals else ""))
    lines += [f"- {group.name}: {group.deals}" for group in placed.by_zone]

    lines += ["", f"On the market for more than {STAGNANT_MIN_DAYS} days: {len(stuck)}"]
    for item in stuck[:STAGNANT_SHOWN]:
        demand = f", {item.demand}" if item.demand else ""
        lines.append(f"- {item.title} ({item.zone}): {item.days} days{demand}")
    if len(stuck) > STAGNANT_SHOWN:
        lines.append(f"- and {len(stuck) - STAGNANT_SHOWN} more")

    lines += ["", "Unmet demand:"]
    lines += [_unmet_line(item) for item in unmet] or ["- no searches without results"]

    lines += ["", f"Details in the panel: {panel_url}{BUSINESS_PATH}"]
    return "\n".join(lines)


def build(session: Session, start: datetime, end: datetime, config: ReportConfig | None = None) -> str:
    """El texto del reporte de la semana `[start, end)`, con las consultas de N-6."""
    config = config or default_config()
    options = {"include_synthetic": config.include_synthetic}
    stuck = [item for item in stagnant(session, start, end, **options) if item.days >= STAGNANT_MIN_DAYS]
    return report_text(start, end, placements(session, start, end, **options), stuck,
                       unmet_demand(session, start, end, limit=UNMET_SHOWN, **options), config.panel_url)


def parse_period(payload: dict) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(payload["start"]), datetime.fromisoformat(payload["end"])

