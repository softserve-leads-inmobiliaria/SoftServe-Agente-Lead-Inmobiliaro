"""Atribución al bot y conversión (N-7). Solo lee de Postgres, con SQL explícito, como el `funnel.py` del panel.

Vista agregada (DT-41): cualquier token de asesor la ve entera, y nunca devuelve un lead. Cuenta leads y
cierres; no lista ids, nombres, teléfonos, correos ni texto de la conversación.

Dos formas de usar el periodo `[start, end)`:
- **Cierres** (`bot_attribution`, `time_to_close`): los de `deals.closed_at` dentro del periodo.
- **Cohortes de leads** (`conversion_by_band`, `campaign_return`): los leads creados dentro del periodo,
  con lo que les pasó después (su banda actual, si calificaron, si cerraron).

`include_synthetic=False` deja fuera la historia sembrada por N-9 (`synthetic = true`); la demo la incluye.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

# DT-41: un cierre es del bot si el lead tocó esa propiedad con el bot en los 60 días previos. Se puede
# cambiar sin migración.
ATTRIBUTION_WINDOW_DAYS = 60
# Interacciones que cuentan para la atribución. `busqueda` sola no: salir en una lista no es interés.
ATTRIBUTING_KINDS = ("anuncio", "consulta", "visita")
BANDS = ("HOT", "WARM", "COLD")

_ATTRIBUTION = text("""
    SELECT count(*) AS deals,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM property_interest pi
               WHERE pi.lead_id = d.lead_id AND pi.property_id = d.property_id
                 AND pi.kind = ANY(:kinds)
                 AND pi.created_at < d.closed_at
                 AND pi.created_at >= d.closed_at - make_interval(days => :window)
           )) AS attributed
    FROM deals d
    WHERE d.closed_at >= :start AND d.closed_at < :end AND (:synthetic OR NOT d.synthetic)
""")

# El lead se crea con su primer mensaje (B-4), así que `leads.created_at` es el primer contacto.
_TIME_TO_CLOSE = text("""
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY delta) AS p50,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY delta) AS p90,
           count(*) AS deals
    FROM (
        SELECT extract(epoch FROM d.closed_at - l.created_at) AS delta
        FROM deals d JOIN leads l ON l.id = d.lead_id
        WHERE d.closed_at >= :start AND d.closed_at < :end AND (:synthetic OR NOT d.synthetic)
    ) AS closes
""")

_BY_BAND = text("""
    SELECT s.band, count(*) AS leads, count(*) FILTER (WHERE l.status = 'closed_won') AS won
    FROM leads l JOIN lead_scores s ON s.lead_id = l.id
    WHERE l.created_at >= :start AND l.created_at < :end AND (:synthetic OR NOT l.synthetic)
    GROUP BY s.band
""")

_BY_CAMPAIGN = text("""
    SELECT l.utm_campaign AS campaign,
           count(*) AS leads,
           count(*) FILTER (WHERE l.qualified_at IS NOT NULL) AS qualified,
           count(*) FILTER (WHERE EXISTS (SELECT 1 FROM deals d WHERE d.lead_id = l.id)) AS closed
    FROM leads l
    WHERE l.created_at >= :start AND l.created_at < :end AND (:synthetic OR NOT l.synthetic)
    GROUP BY l.utm_campaign
    ORDER BY (l.utm_campaign IS NULL), leads DESC, campaign
""")


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


@dataclass(frozen=True)
class BotAttribution:
    deals: int  # cierres del periodo
    attributed: int  # de esos, los que cumplen la regla de DT-41

    @property
    def rate(self) -> float | None:
        """Parte de los cierres que trajo el bot. None si no hubo cierres."""
        return _rate(self.attributed, self.deals)


@dataclass(frozen=True)
class TimeToClose:
    p50: timedelta | None  # None si no hubo cierres
    p90: timedelta | None
    deals: int


@dataclass(frozen=True)
class BandConversion:
    band: str  # HOT, WARM o COLD: la banda actual del lead, no la que tenía al calificar
    leads: int
    won: int  # en `closed_won`

    @property
    def rate(self) -> float | None:
        return _rate(self.won, self.leads)


@dataclass(frozen=True)
class CampaignReturn:
    campaign: str | None  # None: leads que no llegaron por una campaña
    leads: int
    qualified: int
    closed: int  # con al menos una operación en `deals`

    @property
    def close_rate(self) -> float | None:
        return _rate(self.closed, self.leads)


@dataclass(frozen=True)
class AttributionReport:
    start: datetime
    end: datetime
    include_synthetic: bool
    attribution: BotAttribution
    time_to_close: TimeToClose
    by_band: list[BandConversion]
    by_campaign: list[CampaignReturn]


def _params(start: datetime, end: datetime, include_synthetic: bool) -> dict:
    if end <= start:
        raise ValueError("el periodo termina antes de empezar")
    return {"start": start, "end": end, "synthetic": include_synthetic}


def _seconds(value) -> timedelta | None:
    return None if value is None else timedelta(seconds=float(value))


def bot_attribution(session: Session, start: datetime, end: datetime, *,
                    include_synthetic: bool = False) -> BotAttribution:
    """Cierres del periodo y cuántos son del bot (DT-41)."""
    row = session.execute(_ATTRIBUTION, _params(start, end, include_synthetic)
                          | {"window": ATTRIBUTION_WINDOW_DAYS, "kinds": list(ATTRIBUTING_KINDS)}).one()
    return BotAttribution(deals=row.deals, attributed=row.attributed)


def time_to_close(session: Session, start: datetime, end: datetime, *,
                  include_synthetic: bool = False) -> TimeToClose:
    """Del primer contacto del lead a cada cierre del periodo: mediana y p90."""
    row = session.execute(_TIME_TO_CLOSE, _params(start, end, include_synthetic)).one()
    return TimeToClose(p50=_seconds(row.p50), p90=_seconds(row.p90), deals=row.deals)


def conversion_by_band(session: Session, start: datetime, end: datetime, *,
                       include_synthetic: bool = False) -> list[BandConversion]:
    """De los leads creados en el periodo y con score, cuántos llegaron a `closed_won`, por banda. Siempre
    devuelve las tres bandas, en orden HOT, WARM, COLD."""
    counts = {row.band: row for row in session.execute(_BY_BAND, _params(start, end, include_synthetic))}
    return [BandConversion(band=band, leads=counts[band].leads if band in counts else 0,
                           won=counts[band].won if band in counts else 0) for band in BANDS]


def campaign_return(session: Session, start: datetime, end: datetime, *,
                    include_synthetic: bool = False) -> list[CampaignReturn]:
    """Por campaña de origen, de los leads creados en el periodo: cuántos, cuántos calificaron y cuántos
    cerraron. Las campañas con más leads primero; los leads sin campaña al final."""
    return [CampaignReturn(campaign=row.campaign, leads=row.leads, qualified=row.qualified, closed=row.closed)
            for row in session.execute(_BY_CAMPAIGN, _params(start, end, include_synthetic))]


def attribution_report(session: Session, start: datetime, end: datetime, *,
                       include_synthetic: bool = False) -> AttributionReport:
    """Las cuatro métricas del periodo, para la vista de N-8."""
    options = {"include_synthetic": include_synthetic}
    return AttributionReport(
        start=start, end=end, include_synthetic=include_synthetic,
        attribution=bot_attribution(session, start, end, **options),
        time_to_close=time_to_close(session, start, end, **options),
        by_band=conversion_by_band(session, start, end, **options),
        by_campaign=campaign_return(session, start, end, **options),
    )
