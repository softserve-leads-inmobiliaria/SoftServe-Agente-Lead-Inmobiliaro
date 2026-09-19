"""Core falso (CT-7): evento canónico de entrada -> evento canónico de salida con respuesta fija.

Habla exactamente el contrato `contracts/event.schema.json` (CT-1), así que reemplazarlo por el
core real no cambia a quien lo llama. Determinista: mismo evento de entrada, misma salida (el
`event_id` de salida se deriva del de entrada y el `timestamp` se copia; no lee el reloj).
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping

from app.fakes.fake_llm import FakeLLM

_OUTBOUND_NAMESPACE = uuid.UUID("6f1c1d2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f")
_REQUIRED = ("schema_version", "event_id", "conversation_id", "lead_id", "channel",
             "direction", "text", "timestamp")


class FakeCore:
    def __init__(self, llm: FakeLLM | None = None) -> None:
        self._llm = llm or FakeLLM()

    def handle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        missing = [field for field in _REQUIRED if field not in event]
        if missing:
            raise ValueError(f"evento incompleto, faltan: {missing}")
        if event["direction"] != "inbound":
            raise ValueError("el core solo procesa eventos inbound")

        intent = self._llm.classify_intent(event["text"])
        return {
            "schema_version": event["schema_version"],
            "event_id": str(uuid.uuid5(_OUTBOUND_NAMESPACE, str(event["event_id"]))),
            "conversation_id": event["conversation_id"],
            "lead_id": event["lead_id"],
            "channel": event["channel"],
            "direction": "outbound",
            "text": self._llm.generate_reply(intent),
            "timestamp": event["timestamp"],
        }
