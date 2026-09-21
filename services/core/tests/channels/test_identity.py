"""B-4: `telegram_user_id` resuelve siempre al mismo `Lead`, incluso con mensajes simultáneos."""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from app.channels.identity import resolve_identity, resolve_lead
from db.models import Conversation, Lead
from db.session import session_scope


def _leads_con(engine, telegram_user_id: str) -> int:
    with session_scope(engine) as session:
        return session.execute(
            select(func.count()).select_from(Lead).where(Lead.telegram_user_id == telegram_user_id)
        ).scalar_one()


def test_un_usuario_nuevo_crea_su_lead(engine):
    identity = resolve_identity(engine, "tg-nuevo")
    assert _leads_con(engine, "tg-nuevo") == 1
    with session_scope(engine) as session:
        assert session.get(Conversation, identity.conversation_id).lead_id == identity.lead_id


def test_el_mismo_usuario_siempre_resuelve_al_mismo_lead(engine):
    primera = resolve_identity(engine, "tg-repetido")
    segunda = resolve_identity(engine, "tg-repetido")
    assert primera.lead_id == segunda.lead_id
    assert primera.conversation_id == segunda.conversation_id
    assert _leads_con(engine, "tg-repetido") == 1


def test_una_conversacion_terminal_no_se_reusa(engine):
    primera = resolve_identity(engine, "tg-terminal")
    with session_scope(engine) as session:
        session.execute(update(Conversation)
                        .where(Conversation.id == primera.conversation_id)
                        .values(fsm_state="HANDOFF"))

    segunda = resolve_identity(engine, "tg-terminal")
    assert segunda.lead_id == primera.lead_id
    assert segunda.conversation_id != primera.conversation_id


def test_dos_primeros_mensajes_simultaneos_crean_un_solo_lead(database_url):
    """La carrera real: dos turnos del mismo usuario nuevo entrando a la vez."""
    telegram_user_id = "tg-carrera"
    barrier = threading.Barrier(2)
    resultados: list[uuid.UUID] = []
    errores: list[BaseException] = []
    engine = create_engine(database_url)
    Session = sessionmaker(engine)

    def resolver() -> None:
        try:
            with Session() as session:
                barrier.wait(timeout=10)
                resultados.append(resolve_lead(session, telegram_user_id))
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo principal
            errores.append(exc)

    hilos = [threading.Thread(target=resolver) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=15)

    assert not errores, errores
    assert len(set(resultados)) == 1
    assert _leads_con(engine, telegram_user_id) == 1
    engine.dispose()
