"""Historial de estados de las propiedades (N-1).

`set_status` es la única vía para cambiar `Property.status`: lo cambia y escribe su fila en
`property_status_events` en la misma sesión, así el historial nunca queda desfasado del estado. La
usan el cierre de operaciones del panel (N-3) y la historia sintética (N-9). No hace commit: la
transacción es de quien llama.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from db.models import PROPERTY_STATUSES, Property, PropertyStatusEvent


class UnknownProperty(LookupError):
    """La propiedad no existe."""


class StatusUnchanged(ValueError):
    """La propiedad ya está en ese estado: p. ej., cerrar una propiedad ya `cerrado` (N-3)."""


def set_status(session: Session, property_id: str, status: str, actor: str, *,
               at: datetime | None = None, synthetic: bool = False) -> PropertyStatusEvent:
    """Pasa la propiedad a `status` y registra el cambio, con `actor` (p. ej. el id del asesor).

    `at` es la fecha del evento; sin ella, la hora de la base. N-9 la usa para sembrar fechas pasadas.
    Bloquea la fila de la propiedad hasta el fin de la transacción: dos cierres simultáneos de la
    misma propiedad no pueden pasar los dos.
    """
    if status not in PROPERTY_STATUSES:
        raise ValueError(f"estado desconocido: {status!r}")
    prop = session.get(Property, property_id, with_for_update=True)
    if prop is None:
        raise UnknownProperty(property_id)
    if prop.status == status:
        raise StatusUnchanged(f"{property_id} ya está en {status!r}")
    event = PropertyStatusEvent(property_id=property_id, from_status=prop.status, to_status=status,
                                actor=actor, synthetic=synthetic)
    if at is not None:
        event.changed_at = at
    prop.status = status
    session.add(event)
    session.flush()
    return event


def record_listing(session: Session, property_id: str, actor: str, *,
                   at: datetime | None = None, synthetic: bool = False) -> PropertyStatusEvent:
    """Registra el alta de la propiedad en su estado actual (`from_status` nulo), sin cambiarlo.

    Es el punto de partida de los días en el mercado (N-6) para una propiedad que se publica.
    """
    prop = session.get(Property, property_id)
    if prop is None:
        raise UnknownProperty(property_id)
    event = PropertyStatusEvent(property_id=property_id, from_status=None, to_status=prop.status,
                                actor=actor, synthetic=synthetic)
    if at is not None:
        event.changed_at = at
    session.add(event)
    session.flush()
    return event
