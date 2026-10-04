"""Métricas del funnel para el dashboard (B-9). Solo lee de Postgres, nunca de las trazas (DT-18).

Vista global del negocio: cualquier asesor la ve entera, con cortes por zona y por asesor. Es una
excepción deliberada al aislamiento por zona del resto del panel — son 3 asesores conocidos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

# Etapas en el orden del camino feliz (design.md §2); las laterales después. `GREETING` no se
# registra en funnel_events (DT-17): el total de leads que empezaron sale de `leads`.
HAPPY_PATH = ("DISCOVERY", "QUALIFY", "RECOMMEND", "SCHEDULE", "HANDOFF")
SIDE_STATES = ("HUMAN_HANDOFF", "NURTURE", "LOST")

# La consulta canónica de DT-17, la misma de services/core/tests/test_funnel_events.py.
_STAGES = text("""
    SELECT event_type, count(DISTINCT lead_id) AS leads
    FROM funnel_events
    GROUP BY event_type
""")

# Sin los leads de la historia sintética (N-9): el funnel es de la operación real, y esos leads no tienen
# conversaciones ni `funnel_events`.
_TOTAL_LEADS = text("SELECT count(*) FROM leads WHERE NOT synthetic")

# Tiempo hasta la primera respuesta del agente en cada conversación: mediana y p90, en segundos.
_FIRST_RESPONSE = text("""
    SELECT
        percentile_cont(0.5) WITHIN GROUP (ORDER BY delta) AS p50,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY delta) AS p90,
        count(*) AS conversations
    FROM (
        SELECT extract(epoch FROM min(m.created_at) - c.started_at) AS delta
        FROM conversations c
        JOIN messages m ON m.conversation_id = c.id AND m.role = 'assistant'
        GROUP BY c.id
    ) AS first_replies
""")

# Contención: leads que nunca pasaron por HUMAN_HANDOFF, sobre el total.
_HANDED_OFF = text("""
    SELECT count(DISTINCT lead_id) FROM funnel_events WHERE event_type = 'state.HUMAN_HANDOFF'
""")

_BY_ZONE = text("""
    SELECT coalesce(l.zone, 'sin zona') AS zone,
           count(*) AS leads,
           count(*) FILTER (WHERE s.band = 'HOT') AS hot,
           count(*) FILTER (WHERE s.band = 'WARM') AS warm,
           count(*) FILTER (WHERE l.status IN ('sql', 'visit_scheduled', 'closed_won')) AS managed
    FROM leads l
    LEFT JOIN lead_scores s ON s.lead_id = l.id
    WHERE NOT l.synthetic
    GROUP BY coalesce(l.zone, 'sin zona')
    ORDER BY leads DESC, zone
""")

_BY_ADVISOR = text("""
    SELECT coalesce(a.name, 'sin asignar') AS advisor,
           a.zone AS advisor_zone,
           count(*) AS leads,
           count(*) FILTER (WHERE s.band = 'HOT') AS hot,
           count(*) FILTER (WHERE s.band = 'WARM') AS warm,
           count(*) FILTER (WHERE l.status IN ('sql', 'visit_scheduled', 'closed_won')) AS managed
    FROM leads l
    LEFT JOIN advisors a ON a.id = l.advisor_id
    LEFT JOIN lead_scores s ON s.lead_id = l.id
    WHERE NOT l.synthetic
    GROUP BY a.id, a.name, a.zone
    ORDER BY (a.id IS NULL), leads DESC, advisor
""")


@dataclass(frozen=True)
class Stage:
    name: str
    leads: int
    share: float  # sobre el total de leads que empezaron


@dataclass(frozen=True)
class FirstResponse:
    p50: timedelta | None
    p90: timedelta | None
    conversations: int


@dataclass(frozen=True)
class Breakdown:
    label: str
    zone: str | None
    leads: int
    hot: int
    warm: int
    managed: int


@dataclass(frozen=True)
class FunnelReport:
    total_leads: int
    happy_path: list[Stage]
    side_states: list[Stage]
    first_response: FirstResponse
    handed_off: int
    by_zone: list[Breakdown]
    by_advisor: list[Breakdown]

    @property
    def containment_rate(self) -> float | None:
        """Leads atendidos sin traspaso a un humano. None si todavía no hay leads."""
        if self.total_leads == 0:
            return None
        return (self.total_leads - self.handed_off) / self.total_leads


def _seconds(value) -> timedelta | None:
    return None if value is None else timedelta(seconds=float(value))


def funnel_report(session: Session) -> FunnelReport:
    total = session.execute(_TOTAL_LEADS).scalar_one()
    counts = {row.event_type: row.leads for row in session.execute(_STAGES)}

    def stage(name: str) -> Stage:
        leads = counts.get(f"state.{name}", 0)
        return Stage(name=name, leads=leads, share=(leads / total) if total else 0.0)

    first = session.execute(_FIRST_RESPONSE).one()
    return FunnelReport(
        total_leads=total,
        happy_path=[stage(name) for name in HAPPY_PATH],
        side_states=[stage(name) for name in SIDE_STATES],
        first_response=FirstResponse(p50=_seconds(first.p50), p90=_seconds(first.p90),
                                     conversations=first.conversations),
        handed_off=session.execute(_HANDED_OFF).scalar_one(),
        by_zone=[Breakdown(label=r.zone, zone=None, leads=r.leads, hot=r.hot, warm=r.warm, managed=r.managed)
                 for r in session.execute(_BY_ZONE)],
        by_advisor=[Breakdown(label=r.advisor, zone=r.advisor_zone, leads=r.leads, hot=r.hot,
                              warm=r.warm, managed=r.managed)
                    for r in session.execute(_BY_ADVISOR)],
    )
