"""Transcripción de la conversación en `messages` (B-5).

Se escribe dentro de la transacción del turno, junto al estado de la FSM: una conversación no puede
quedar con el estado movido y sin el mensaje que lo movió.

El texto que se guarda es el que ya pasó por el guardrail de entrada (A-8), nunca el crudo: la PII
no solicitada que el guardrail redactó no puede volver a entrar por acá (`docs/DESIGN/guardrails.md`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from db.models import Message


def record_turn(session: Session, conversation_id: uuid.UUID, *, inbound_text: str,
                outbound_text: str) -> None:
    """Guarda el mensaje del lead y la respuesta del agente, en ese orden.

    Los `created_at` se fijan acá en vez de dejar el `now()` de la columna: `now()` en Postgres es la
    hora de la transacción, así que ambas filas del turno empatarían y el orden de la transcripción
    (B-5) quedaría indefinido. La respuesta va un microsegundo después, que es además lo que pasó.
    """
    moment = datetime.now(timezone.utc)
    rows = ((("user", inbound_text), moment), (("assistant", outbound_text), moment + timedelta(microseconds=1)))
    for (role, content), created_at in rows:
        if content:
            session.add(Message(conversation_id=conversation_id, role=role, content=content,
                                created_at=created_at))
