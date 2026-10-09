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

    {"text": "Hi, good afternoon", "expect_reply_contains": "assistant"}
    {"text": "I'd like to schedule a visit", "expect_reply_contains": "visit"}

## Qué valida en cada turno

1. El evento `inbound` construido cumple `contracts/event.schema.json`.
2. La respuesta también cumple el contrato, es `outbound` y conserva `conversation_id` y `lead_id`.
3. La respuesta contiene lo indicado en `expect_reply_contains`.

## Cómo correrlo

    python harness/scripts/replay.py harness/fixtures/saludo_y_busqueda.jsonl   # un guion
    python harness/scripts/run_all.py                                            # todos

Con `CORE_URL` los dos hablan con el core por HTTP; cada turno espera hasta `HARNESS_HTTP_TIMEOUT`
segundos (60 por defecto). Una respuesta fuera de contrato, que no es JSON o que no llega cuenta como
FALLA de ese guion y la corrida sigue con los demás.

## Corrida real (OBS-6)

    python harness/scripts/live_run.py               # contra el stack de docker compose
    python harness/scripts/live_run.py --sin-frases  # con LLM_PROVIDER=bedrock

Reproduce todos los guiones contra los servicios levantados y además comprueba lo que dejaron: el
estado final de la FSM en Postgres (`harness/scripts/expectations.py`) y, si el lead quedó con zona, que
el `panel` lo muestre en la cola del asesor de esa zona con su etapa y la transcripción en su ficha.
Cada corrida deriva los ids de un `run_id` propio, así que se puede repetir sobre la misma base. Deja un
informe en `tmp/harness/`. `--sin-frases` no exige `expect_reply_contains`, pero sí el contrato y que
la respuesta tenga texto. Cómo levantar el stack: `docs/TESTING.md`, "Corrida real".

## Evaluación en vivo repetible (#216)

    python harness/scripts/evaluate.py --reps 5      # con LLM_PROVIDER=bedrock

Los guiones de `fixtures/` fijan las frases del agente *fake*. La evaluación usa otros escenarios
(`scripts/scenarios.py`, los mismos de `scripts/demo/conversar.py`) que esperan lo que tiene que pasar con
cualquier modelo: qué tool consulta cada turno, qué texto aparece o no puede aparecer, qué auditoría deja y
en qué estado termina. Los corre N veces y calcula, con lo que quedó en Postgres, las tasas de turnos OK,
de tool esperada, de regeneraciones, de respuestas degradadas y de respaldo, y la latencia
(`scripts/evaluation.py`, con sus umbrales). El informe se reescribe después de cada conversación, y
`scripts/tokens.py <informe>.json` agrega los tokens y las llamadas al LLM, leídos de Phoenix. Detalle en
`docs/TESTING.md`, "Evaluación en vivo repetible".

## Prueba de jurado (#247)

    python harness/scripts/jury.py M                 # con LLM_PROVIDER=bedrock

Son conversaciones no pautadas, como las de un jurado: los sets de desarrollo J, N, M y O y el reservado P,
en `scripts/jury_scenarios.py`. `jury.py` deja la transcripción con las auditorías y la decisión de cada turno,
para leerla a mano. Los mismos sets llevan expectativas por turno, así que `evaluate.py J N M O` (o `P`) los
mide con las reglas de la evaluación en vivo. El clasificador tiene su propio gold set: `gold/classifier.jsonl`,
que mide `scripts/classifier_gold.py` contra `gold/classifier_baseline.json`. Detalle en `docs/TESTING.md`,
"Prueba de jurado" y "Medir sin leer a mano".

## Agregar un guion nuevo

Crea `harness/fixtures/<nombre>.jsonl` y declara su estado final en `harness/scripts/expectations.py`
(un test lo exige). Nada más: `run_all.py`, `live_run.py` y la suite `tests/harness` descubren todos
los `*.jsonl` de esa carpeta. Convención: una fixture nueva por cada historia de `core` que cambie el
comportamiento conversacional.
