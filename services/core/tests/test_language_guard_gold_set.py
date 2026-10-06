"""Gold set del filtro de idioma (#202, DT-55): qué mensaje recibe el aviso de "solo en inglés".

El criterio: ningún mensaje en inglés recibe el aviso (rechazar a quien ya escribe en inglés es el error
caro), y los mensajes en español típicos de un lead sí lo reciben. Los nombres de ciudades, zonas, calles y
propiedades no cuentan como español.
"""
from __future__ import annotations

import pytest

from app.input_guard import inspect_input
from app.language_guard import is_spanish

ESPANOL = [
    "Hola", "hola, buenas tardes", "Buenos días", "Gracias!", "Perfecto, gracias", "Sí", "¿Cuánto cuesta?",
    "Busco casa en El Poblado", "Busco un apartamento en Laureles", "Quiero agendar una visita",
    "¿Tienen algo en Chapinero?", "Envigado, 2 habitaciones", "3 habitaciones", "Mi presupuesto es de 300 millones",
    "Quiero hablar con un asesor", "¿Está disponible?", "Necesito algo para este mes", "decido yo",
    "busco depa para rentar cerca del metro", "me interesa la del Poblado", "¿Me puedes mandar fotos?",
    "Mañana en la tarde", "el martes a las 10", "la segunda", "Soy colombiano y busco depa",
    "¿Aceptan mascotas?", "Hola, quisiera información de la casa en Usaquén", "busco casa en la zona norte",
]

INGLES = [
    "Hi", "hello", "Thanks!", "yes please", "ok", "no", "sounds good", "the second one", "at 4 pm", "2nd",
    "Tomorrow afternoon", "I'm looking for a house in El Poblado", "Do you have anything in Chapinero?",
    "I'd like to schedule a viewing", "my budget is $300k", "How much does it cost?", "I want to talk to an agent",
    "Is it still available?", "I need something this month", "I decide", "I'm Colombian",
    "Hola! I'm looking for a 2 bedroom in Laureles",  # un saludo en español no hace español al mensaje
    "I want a casa in Envigado", "near Parque de la 93", "Can you send me photos?", "Do you accept pets?",
    "What about the one in Usaquén?", "Is the apartment on Carrera 37 still for rent?",
]

# Nombres propios: ni español ni inglés, pasan.
NOMBRES = [
    "Laureles", "El Poblado", "Usaquén", "Medellín o Bogotá", "en Chapinero", "La Candelaria", "Santa Fe de Antioquia",
    "Calle 10", "Carrera 37 # 8A-24, El Poblado", "Cra 43A # 1-50", "prop_231", "", "   ", "👍", "300000",
]

# (mensaje, recibe el aviso): lo que cuenta el gate de gold sets (OBS-4).
CASES = [(text, True) for text in ESPANOL] + [(text, False) for text in INGLES + NOMBRES]


@pytest.mark.parametrize("text", ESPANOL)
def test_spanish_gets_the_notice(text):
    assert is_spanish(text)


@pytest.mark.parametrize("text", INGLES + NOMBRES)
def test_english_and_names_pass(text):
    assert not is_spanish(text)


def test_the_gold_set_meets_its_thresholds():
    """0 falsos positivos en inglés y en nombres; todo el español típico detectado."""
    false_positives = [t for t in INGLES + NOMBRES if is_spanish(t)]
    missed = [t for t in ESPANOL if not is_spanish(t)]
    assert false_positives == [] and missed == []


def test_the_verdict_flags_and_audits_spanish():
    verdict = inspect_input("Busco casa en El Poblado")
    assert verdict.not_english and verdict.flagged
    assert ("input.not_english", {}) in verdict.audit_kinds()


def test_opt_out_comes_first_even_in_spanish():
    verdict = inspect_input("ya no me escriban")
    assert verdict.opt_out and not verdict.not_english


def test_pii_and_protected_categories_still_apply_to_spanish():
    verdict = inspect_input("Soy católico, mi cédula es 1.234.567.890")
    assert verdict.not_english and verdict.pii and verdict.protected_category
    assert "1.234.567.890" not in verdict.signal_text
