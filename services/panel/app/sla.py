"""Umbral de atención de los leads HOT (B-12).

Un lead HOT que nadie tomó ("en gestión", B-6) y que calificó hace más de `SLA_HOT_MINUTES` minutos se marca
en la cola. Solo se marca: el aviso activo por Telegram queda fuera de B-12.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

DEFAULT_MINUTES = 30
UNCLAIMED = ("nuevo", "mql")


def hot_wait_from_env() -> timedelta:
    """`SLA_HOT_MINUTES`, un entero mayor que cero. Un valor inválido falla al arrancar el panel."""
    raw = os.environ.get("SLA_HOT_MINUTES", "").strip()
    if not raw:
        return timedelta(minutes=DEFAULT_MINUTES)
    try:
        minutes = int(raw)
    except ValueError:
        raise ValueError(f"SLA_HOT_MINUTES debe ser un entero de minutos, no {raw!r}") from None
    if minutes <= 0:
        raise ValueError("SLA_HOT_MINUTES debe ser mayor que cero")
    return timedelta(minutes=minutes)


def overdue(lead, now: datetime, limit: timedelta) -> timedelta | None:
    """Cuánto lleva esperando un HOT sin tomar, si pasó el umbral; None si no hay que marcarlo."""
    if lead.score_band != "HOT" or lead.status not in UNCLAIMED or lead.qualified_at is None:
        return None
    waiting = now - lead.qualified_at
    return waiting if waiting > limit else None
