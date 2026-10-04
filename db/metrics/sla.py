"""Tiempo de atención del asesor (B-12). Solo lee de Postgres, con SQL explícito, como `attribution.py`.

Para el gerente: cuánto tarda cada asesor en tomar un lead calificado, para que ningún HOT se enfríe
esperando. Vista agregada (DT-41): cuenta leads por asesor, no los lista.

Reglas de conteo:
- La **toma** es `leads.claimed_at`: la primera vez que un asesor lo marcó "en gestión" en el panel (B-6).
- El tiempo va de `qualified_at` (cruzó a WARM o HOT, B-7) a la toma. Un lead tomado antes de calificar
  no cuenta: no estaba esperando a nadie.
- El asesor es el del lead (`leads.advisor_id`), el responsable de atenderlo, aunque lo haya tomado otro
  asesor de la zona.
- Entran los leads **tomados** dentro del periodo `[start, end)`. La banda es la actual del lead.

`include_synthetic=False` deja fuera los leads sembrados por N-9.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

_CLAIM_TIMES = text("""
    SELECT coalesce(a.name, l.advisor_id) AS advisor,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM l.claimed_at - l.qualified_at)) AS p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM l.claimed_at - l.qualified_at))
               FILTER (WHERE s.band = 'HOT') AS p50_hot,
           count(*) AS leads,
           count(*) FILTER (WHERE s.band = 'HOT') AS hot
    FROM leads l
    LEFT JOIN advisors a ON a.id = l.advisor_id
    LEFT JOIN lead_scores s ON s.lead_id = l.id
    WHERE l.claimed_at >= :start AND l.claimed_at < :end
      AND l.qualified_at IS NOT NULL AND l.claimed_at >= l.qualified_at
      AND l.advisor_id IS NOT NULL
      AND (:synthetic OR NOT l.synthetic)
    GROUP BY a.name, l.advisor_id
    ORDER BY p50 DESC, advisor
""")


@dataclass(frozen=True)
class ClaimTime:
    advisor: str
    median: timedelta  # de todos los calificados que tomó en el periodo
    median_hot: timedelta | None  # solo los HOT; None si no tomó ninguno
    leads: int
    hot: int


def _seconds(value) -> timedelta | None:
    return None if value is None else timedelta(seconds=float(value))


def claim_times(session: Session, start: datetime, end: datetime, *,
                include_synthetic: bool = False) -> list[ClaimTime]:
    """Mediana por asesor del tiempo entre que el lead calificó y su toma. Los más lentos primero."""
    if end <= start:
        raise ValueError("el periodo termina antes de empezar")
    params = {"start": start, "end": end, "synthetic": include_synthetic}
    return [ClaimTime(advisor=row.advisor, median=_seconds(row.p50), median_hot=_seconds(row.p50_hot),
                      leads=row.leads, hot=row.hot)
            for row in session.execute(_CLAIM_TIMES, params)]
