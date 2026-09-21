"""B-5: cada turno deja su transcripción en `messages`, ya saneada por el guardrail."""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.input_guard import REDACTED
from app.turn import process_turn
from db.repo_root import repo_root

INBOUND = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))


class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": [{"title": "Casa de prueba"}]}


class Chat:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, message):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message}
        return asyncio.run(process_turn(self.engine, event, StubCatalog()))

    @property
    def transcript(self):
        with self.engine.connect() as connection:
            return connection.execute(
                text("SELECT role, content FROM messages WHERE conversation_id = :c"
                     " ORDER BY created_at, id"),
                {"c": self.conversation_id}).all()


def test_un_turno_guarda_el_mensaje_del_lead_y_la_respuesta(engine):
    chat = Chat(engine)
    reply = chat.say("Hola")

    assert chat.transcript == [("user", "Hola"), ("assistant", reply["text"])]


def test_la_transcripcion_conserva_el_orden_cronologico(engine):
    chat = Chat(engine)
    chat.say("Hola")
    chat.say("Busco un departamento")

    roles = [role for role, _ in chat.transcript]
    contenidos = [content for _, content in chat.transcript]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert contenidos[0] == "Hola"
    assert contenidos[2] == "Busco un departamento"


def test_la_pii_no_solicitada_se_guarda_redactada(engine):
    """El guardrail A-8 redacta antes de responder; la transcripción no puede reintroducir el dato.

    Teléfono y correo no entran acá: son datos que el producto sí pide, con consentimiento (B-2).
    """
    chat = Chat(engine)
    chat.say("Mi RFC es GODE561231GR8")

    guardado = chat.transcript[0][1]
    assert "GODE561231GR8" not in guardado
    assert REDACTED in guardado


def test_la_baja_tambien_queda_en_la_transcripcion(engine):
    chat = Chat(engine)
    chat.say("Hola")
    chat.say("STOP")

    assert [role for role, _ in chat.transcript] == ["user", "assistant", "user", "assistant"]
    assert chat.transcript[2][1] == "STOP"


def test_un_lead_ya_dado_de_baja_no_suma_transcripcion(engine):
    """Ese camino corta antes de abrir transacción: el lead pidió no ser procesado."""
    chat = Chat(engine)
    chat.say("STOP")
    antes = len(chat.transcript)
    chat.say("¿Hola?")

    assert len(chat.transcript) == antes
