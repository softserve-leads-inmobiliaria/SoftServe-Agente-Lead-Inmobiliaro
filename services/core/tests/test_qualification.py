"""B-7: el lead pasa a calificado una sola vez, cuando la banda cruza a WARM/HOT."""
import asyncio
import json
import uuid

import pytest
from sqlalchemy import text

from app.qualification import just_qualified
from app.turn import process_turn
from db.repo_root import repo_root

INBOUND = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))

# Presupuesto + quién decide + urgencia: 60 puntos, WARM, en un solo turno con el fake LLM.
CALIFICA = "Tengo presupuesto de 3 millones, decido yo y es urgente"


@pytest.mark.parametrize("previous, current, expected", [
    (None, "WARM", True),
    (None, "HOT", True),
    ("COLD", "WARM", True),
    ("COLD", "HOT", True),
    (None, "COLD", False),
    ("COLD", "COLD", False),
    ("WARM", "WARM", False),
    ("WARM", "HOT", False),   # ya estaba calificado: no es un cruce nuevo
    ("HOT", "WARM", False),
    ("WARM", "COLD", False),
])
def test_just_qualified_solo_en_el_cruce(previous, current, expected):
    assert just_qualified(previous, current) is expected


class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": []}

    async def tool_specs(self):
        return []


class Chat:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.notified = []

    async def _on_qualified(self, event, transition):
        self.notified.append(transition)

    def say(self, message, *, with_hook=True):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message}
        return asyncio.run(process_turn(self.engine, event, StubCatalog(),
                                        on_qualified=self._on_qualified if with_hook else None))

    def lead(self):
        with self.engine.connect() as connection:
            return connection.execute(
                text("SELECT l.status, c.lead_qualified FROM leads l"
                     " JOIN conversations cv ON cv.lead_id = l.id"
                     " JOIN fsm_context c ON c.conversation_id = cv.id WHERE l.id = :l"),
                {"l": self.lead_id}).one()


def test_un_lead_frio_no_dispara_nada(engine):
    chat = Chat(engine)
    chat.say("Hola")

    assert chat.notified == []
    assert chat.lead() == ("nuevo", False)


def test_al_cruzar_a_warm_se_notifica_y_queda_marcado(engine):
    chat = Chat(engine)
    chat.say("Hola")
    chat.say(CALIFICA)

    assert len(chat.notified) == 1
    assert chat.notified[0].qualified is True
    assert chat.notified[0].score.band == "WARM"
    assert chat.lead() == ("mql", True)


def test_los_turnos_siguientes_no_vuelven_a_notificar(engine):
    """El AC: 'no antes, no en un job aparte' — y tampoco dos veces."""
    chat = Chat(engine)
    chat.say(CALIFICA)
    chat.say("¿Y tienen algo más grande?")
    chat.say("Es urgente de verdad")

    assert len(chat.notified) == 1


def test_sin_hook_el_turno_marca_pero_no_notifica(engine):
    """Es el camino del arnés (`POST /events`): califica en la base, no manda mensajes a nadie."""
    chat = Chat(engine)
    chat.say(CALIFICA, with_hook=False)

    assert chat.notified == []
    assert chat.lead() == ("mql", True)


def test_un_lead_reclamado_antes_de_calificar_igual_notifica_y_no_pierde_su_status(engine):
    """#89: B-6 puede mover `status` a 'sql' (reclamado a mano) antes de que el score cruce a
    WARM/HOT. La guarda de `qualify()` no puede depender de `status='nuevo'` para eso, ni pisar
    el `sql` ya ganado por B-6 al marcar calificado."""
    chat = Chat(engine)
    chat.say("Hola")
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET status = 'sql' WHERE id = :l"), {"l": chat.lead_id})

    chat.say(CALIFICA)
    assert len(chat.notified) == 1
    assert chat.notified[0].qualified is True
    assert chat.lead() == ("sql", True)

    chat.say("¿Y tienen algo más grande?")
    assert len(chat.notified) == 1  # no se repite aunque status se haya quedado fijo en 'sql'


def test_un_hook_que_falla_no_rompe_el_turno(engine):
    class Explota(Chat):
        async def _on_qualified(self, event, transition):
            raise RuntimeError("telegram caído")

    chat = Explota(engine)
    reply = chat.say(CALIFICA)

    assert reply["direction"] == "outbound"
    assert chat.lead() == ("mql", True)
