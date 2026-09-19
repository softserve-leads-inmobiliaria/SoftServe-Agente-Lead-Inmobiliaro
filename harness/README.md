# Arnés de canal

Reproduce una conversación de guion (`.jsonl`) como eventos canónicos, sin Telegram real. Sirve para
probar conversaciones completas desde el primer sprint contra el fake core (CT-7) y, más adelante,
contra el core real.

## Formato del guion (`harness/fixtures/<nombre>.jsonl`)

Un archivo por conversación. **Una línea = un turno del lead** (un objeto JSON). Las líneas en
blanco se ignoran.

| Campo | Requerido | Descripción |
|---|---|---|
| `text` | sí | Mensaje del lead (string; puede ser vacío, p. ej. deep-link sin texto). |
| `property_id` | no | Propiedad del deep-link (mismo campo que `contracts/event.schema.json`). |
| `utm_campaign` | no | Campaña de origen (ídem). |
| `expect_reply_contains` | no | String o lista de strings que deben aparecer en la respuesta (sin distinguir mayúsculas). |

Cualquier otro campo es un error. El arnés completa el resto del evento canónico de forma
determinista: `event_id`, `conversation_id` y `lead_id` se derivan del nombre del archivo, y el
`timestamp` avanza 30 s por turno desde una fecha fija. Así dos corridas dan exactamente los mismos
eventos.

Ejemplo:

    {"text": "Hola, buenas tardes", "expect_reply_contains": "asistente"}
    {"text": "Quiero agendar una visita", "expect_reply_contains": "visita"}

## Qué valida en cada turno

1. El evento `inbound` construido cumple `contracts/event.schema.json`.
2. La respuesta también cumple el contrato, es `outbound` y conserva `conversation_id` y `lead_id`.
3. La respuesta contiene lo indicado en `expect_reply_contains`.

## Cómo correrlo

    python harness/scripts/replay.py harness/fixtures/saludo_y_busqueda.jsonl   # un guion
    python harness/scripts/run_all.py                                            # todos

## Agregar un guion nuevo

Crea `harness/fixtures/<nombre>.jsonl`. No hay que tocar código: `run_all.py` y la suite
`tests/harness` descubren todos los `*.jsonl` de esa carpeta. Convención: una fixture nueva por cada
historia de `core` que cambie el comportamiento conversacional.
