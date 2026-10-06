"""Vista de inventario y negocio (N-8): el periodo que pide el gerente y los reportes de ese periodo.

Las consultas son las de N-6, N-7 y B-12 (`db/metrics/`, DT-47); las de N-6 son las del reporte semanal de N-10. Solo
agregados (DT-41): ninguna devuelve un lead, y la lista de estancadas lista propiedades.

El periodo se elige con `periodo`:
- `semana` (por defecto): los últimos 7 días, hoy incluido, en hora local.
- `mes`: los últimos 30 días, hoy incluido.
- `rango`: de `desde` a `hasta`, los dos incluidos (fechas `AAAA-MM-DD`).

Un rango que no se entiende o que termina antes de empezar no rompe la página: se muestra la última
semana con un aviso.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.attribution import AttributionReport, attribution_report
from app.inventory import LOCAL_TZ, InventoryReport, inventory_report
from db.metrics.sla import ClaimTime, claim_times

PERIODS = {"semana": 7, "mes": 30}
DEFAULT_PERIOD = "semana"
RANGE = "rango"
MAX_RANGE_DAYS = 366
INVALID_RANGE = "That date range is invalid: showing the last week."


@dataclass(frozen=True)
class Period:
    kind: str  # semana, mes o rango
    start: datetime
    end: datetime  # excluido: la medianoche del día siguiente al último
    first_day: date
    last_day: date
    error: str | None = None


@dataclass(frozen=True)
class BusinessView:
    period: Period
    include_synthetic: bool
    inventory: InventoryReport
    attribution: AttributionReport
    claim_times: list[ClaimTime]  # B-12


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


def _days(kind: str, first: date, last: date, error: str | None = None) -> Period:
    return Period(kind=kind, start=_midnight(first), end=_midnight(last + timedelta(days=1)),
                  first_day=first, last_day=last, error=error)


def _last(kind: str, today: date, error: str | None = None) -> Period:
    return _days(kind, today - timedelta(days=PERIODS[kind] - 1), today, error)


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_period(kind: str | None, since: str | None, until: str | None, *, today: date) -> Period:
    """El periodo pedido en la URL. Nunca falla: lo que no se entiende vuelve a la última semana."""
    if kind == RANGE:
        first, last = _parse_day(since), _parse_day(until)
        if first is None or last is None or last < first or (last - first).days >= MAX_RANGE_DAYS:
            return _last(DEFAULT_PERIOD, today, INVALID_RANGE)
        return _days(RANGE, first, last)
    return _last(kind if kind in PERIODS else DEFAULT_PERIOD, today)


def business_view(session: Session, period: Period, *, now: datetime,
                  include_synthetic: bool = False) -> BusinessView:
    """Los reportes de N-6, N-7 y B-12 del periodo. Las estancadas se miden a hoy si el periodo llega a hoy."""
    options = {"include_synthetic": include_synthetic}
    return BusinessView(
        period=period, include_synthetic=include_synthetic,
        inventory=inventory_report(session, period.start, period.end, as_of=min(period.end, now), **options),
        attribution=attribution_report(session, period.start, period.end, **options),
        claim_times=claim_times(session, period.start, period.end, **options),
    )
