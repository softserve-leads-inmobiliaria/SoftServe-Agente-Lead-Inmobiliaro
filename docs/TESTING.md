# Pruebas y entorno local

## Levantar el stack base

Requiere Docker Desktop (Windows/Mac) o Docker Engine + Compose (Linux) **abierto y corriendo**.

```
docker compose up --build
```

No hace falta crear un `.env`: `docker-compose.yml` trae defaults de desarrollo. Copiar
`.env.example` a `.env` solo si se quiere cambiar algún puerto o credencial.

| Servicio | URL / puerto | Qué es |
| :--- | :--- | :--- |
| `postgres` | `localhost:5432` (usuario/clave/BD: ver `.env.example`) | Postgres 16 con `pgvector` |
| `phoenix` | http://localhost:6006 (OTLP/HTTP `/v1/traces`, OTLP/gRPC en `4317`) | Observabilidad (DT-9) |
| `panel` | http://localhost:8080/panel/<token> (`/health` para el estado) | Panel de asesores: lista de leads de su zona y ficha con score y transcripción (B-5, DT-5) |
| `migrate` | — (termina al acabar) | Aplica `alembic upgrade head` (esquema completo, C-3) |
| `seed` | — (termina al acabar) | Siembra los 3 asesores de demo, idempotente, e imprime la URL del panel de cada uno (B-5) |
| `catalog` | solo dentro de la red de Docker (`http://catalog:8000`, MCP en `/mcp`) | Catálogo *fake* como servidor MCP (CT-8) |
| `core` | http://localhost:8000/health (`POST /events`) | Core: turno completo (FSM, guardrails, scoring) con agente *fake* por defecto o LLM real (`LLM_PROVIDER`); arranca el bot de Telegram si hay `TELEGRAM_BOT_TOKEN` |
| `harness` | — (termina al acabar) | Reproduce `harness/fixtures/*.jsonl` contra el core |

El arnés corre solo al levantar el stack: al final imprime `N/N guiones en verde` y termina, sin
apagar el resto. Para obtener su resultado como código de salida (0 = todo en verde), levantando
solo lo que necesita:

```
docker compose run --rm --build harness
```

Para ver el estado persistido de las conversaciones que dejó el arnés:

```
docker compose exec postgres psql -U leads -d leads -c "select id, fsm_state from conversations"
```

Y el funnel que se fue registrando, una fila por transición real (OBS-2, DT-17):

```
docker compose exec postgres psql -U leads -d leads -c "select event_type, count(distinct lead_id) as leads from funnel_events group by event_type order by min(id)"
```

Y el score BANT-E que se acumuló por lead (A-9):

```
docker compose exec postgres psql -U leads -d leads -c "select lead_id, need_fit, budget, authority, timing, eligibility, total, band from lead_scores"
```

`migrate` aplica las migraciones de Alembic (`db/migrations`) sobre Postgres y termina; los
servicios que usen la base dependerán de él.

### Probar el bot de Telegram (B-1..B-4)

Con un bot creado en @BotFather: poner `TELEGRAM_BOT_TOKEN` en el `.env` y `docker compose up -d --build core`
(*long polling*, no hace falta URL pública). Sin token, `core` arranca igual y no levanta el bot. Comprobar:

1. Un saludo → aviso de privacidad más la respuesta del agente.
2. `https://t.me/<bot>?start=prop_00231` → el `property_id` llega en el evento.
3. `STOP` y `/stop` → `consent_status=withdrawn` y `status=closed_lost` en `leads`, sin pasar por el LLM.
4. Un segundo `getUpdates` externo devuelve `409 Conflict`: confirma que el *polling* corre de verdad.

### Ver el panel de asesores (B-5)

`docker compose up -d --build` ejecuta `seed`, que imprime una línea por asesor con su URL:
`docker compose logs seed` → `adv_001  Valeria Ortiz  Providencia  /panel/<token>`. Abrir
`http://localhost:8080/panel/<token>`. El panel filtra por `Lead.zone`, y hoy el core no rellena esa columna
(llegará con A-10): para verlo con datos, asignar la zona a mano
(`update leads set zone='Providencia' where ...`). Un token inventado y un lead de otra zona dan el mismo 404.

### Usar el LLM real (Bedrock)

Por defecto el agente es el *fake* (`LLM_PROVIDER=fake`): respuestas fijas y sin credenciales. Para
usar Bedrock (A-2, DT-19) en el `.env`:

```
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID_CHAT=<modelo conversacional>
BEDROCK_MODEL_ID_CLASSIFIER=<modelo liviano>
AWS_ACCESS_KEY_ID=...   AWS_SECRET_ACCESS_KEY=...   (o AWS_PROFILE fuera de Docker)
```

Con inferencia entre regiones el ID lleva prefijo (p. ej. `us.anthropic.claude-haiku-4-5-20251001-v1:0`) y
`BEDROCK_REGION` debe ser una región de EE. UU. Antes de darlos por buenos, probar **una llamada real de un token** con cada ID: algunos modelos de
Bedrock exigen un *inference profile* y el error solo aparece en la primera llamada. Sin los dos IDs,
`core` no arranca (falla con un mensaje claro). Si Bedrock falla o el cupo (10 por minuto y modelo) se
agota, el turno responde con un texto seguro en vez de dejar al lead esperando. Una cuenta con cuota baja
recibe `ThrottlingException` de Bedrock tras pocas llamadas seguidas (el limitador local no lo evita): el
reintento con espera exponencial es la historia A-14, todavía pendiente, y hasta entonces ese error no
queda en el log. Ver DT-19: **con el LLM
real todavía no hay guardrails de salida** (A-5, A-6, A-7), así que no usarlo con leads reales.

El historial de cada conversación se guarda en `messages` (solo el texto ya filtrado, sin PII ni
categorías protegidas):

```
docker compose exec postgres psql -U leads -d leads -c "select role, content from messages order by created_at"
```

### Ver la traza de cada turno (Phoenix)

Con el stack arriba, `core` envía una traza por turno a Phoenix (OBS-1, DT-18). Tras correr el arnés
(`docker compose run --rm --build harness`), abre http://localhost:6006 y entra al proyecto
`lead-agent`. Cada turno es una traza `turn` con:

- `guardrail.input`: qué detectó el guardrail de entrada (`jailbreak`, tipos de PII, fuera de dominio…);
- `catalog.<tool>`: cada llamada al catálogo, con su duración y si falló;
- `fsm.advance`: estado antes y después, *trigger*, motivo del traspaso a humano y score.

Las trazas **no llevan el texto de los mensajes**. Para depurar en local con datos de prueba,
arranca con `TRACE_CONTENT=1`. Si Phoenix no está levantado, `core` responde igual y las trazas se
pierden. Sin `OTEL_EXPORTER_OTLP_ENDPOINT` (p. ej. al correr `uvicorn` a mano) las trazas están apagadas.

Esto es el esqueleto end-to-end de CT-4: arnés → core → catálogo por MCP → respuesta. Todo el
contenido es *fake*, a propósito. Para probar un mensaje a mano con el stack arriba:

```
curl -X POST localhost:8000/events -H "Content-Type: application/json" -d @contracts/examples/event.valid.json
```

Para apagar: `docker compose down`. Para borrar también los datos: `docker compose down -v`.

## Pruebas automáticas

```
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests/contracts       # contratos: no necesitan ningún servicio levantado
python -m pytest tests/infra           # valida docker-compose.yml (sin Docker)
python -m pytest db/tests              # esquema: los de migraciones necesitan Postgres (ver abajo)
python -m pytest services/core/tests
python -m pytest services/catalog/tests
python -m pytest services/panel/tests
python -m pytest tests/harness         # arnés + e2e: levanta catalog y core como procesos, sin Docker
```

### Tests de la base de datos

Los tests de `db/`, `services/core/` y `tests/harness` (estado de la FSM, e2e) usan un Postgres real
mediante `db/testing.py`: una base temporal ya migrada que se borra sola.

`db/tests/test_migrations.py` corre contra un Postgres real: crea una base temporal, aplica
`alembic upgrade head`, prueba constraints y persistencia de la FSM, y la borra. Con Postgres
levantado (`docker compose up -d postgres`) corren solos; sin él se omiten. En CI (`REQUIRE_DB=1`)
nunca se omiten.

Cambiar el esquema: editar `db/models.py` y generar una migración
(`alembic revision --autogenerate -m "descripcion"`, con Postgres arriba); revisarla a mano. El
test `test_migrations_and_models_do_not_drift` falla si los modelos y las migraciones difieren.

Cada suite se corre por separado (los servicios comparten el nombre de paquete `app`); el CI
hace lo mismo en `.github/workflows/ci.yml`.

### Disponibilidad revalidada (A-4)

`services/core/tests/test_availability.py` prueba `AvailabilityGuard` con un catálogo cuyo listado dice
`disponible` para todo pero cuya disponibilidad vigente es otra: una apartada o cerrada nunca se ofrece,
un error al verificar deja la propiedad `sin_verificar`, y el LLM recibe (y `tool_results` guarda) el
mismo resultado ya revalidado. Los tests que pasan por `process_turn` usan `tests/catalog_stub.py`, un
catálogo con `id`, `status` y `get_availability`. Para verlo en vivo: en la traza de un turno con
búsqueda aparecen `catalog.get_availability` además de `catalog.search_properties`.

### Suite de transiciones de la FSM

`services/core/tests/test_fsm_edges.py` (necesita Postgres, como el resto de `core`) protege la FSM
contra la divergencia:

```
python -m pytest services/core/tests/test_fsm_edges.py
```

- **Diagrama, contrato y código.** Lee el diagrama de `docs/DESIGN/design.md` §2 y el enum de
  `contracts/fsm_state.schema.json` y exige que coincidan con `services/core/app/fsm.py`. La única
  arista del código que el diagrama no dibuja es `NURTURE -> HUMAN_HANDOFF`, documentada en DT-12.
- **Una arista, un test.** Cada arista de `TRANSITIONS` se prueba con un mensaje real del lead (o con el
  trigger del *scheduler*) sobre Postgres. Si se añade una arista sin su prueba, la suite falla.
- **Rejilla estado x intención.** Todas las combinaciones, con el resultado esperado escrito a mano:
  también prueba lo que **no** debe moverse.

Al cambiar la FSM hay que tocar los tres sitios a la vez: el diagrama, `fsm.py` y `EDGE_MESSAGES` en
este archivo.

## Caveats de Docker conocidos

Se agregan aquí apenas se descubren.

- **"failed to connect to the docker API ... dockerDesktopLinuxEngine"** (Windows): Docker Desktop
  no está corriendo. Abrirlo y esperar a que diga "Engine running" antes de `docker compose up`.
- **Puerto ocupado** (p. ej. `5432` si hay un Postgres local, o `8080` si otro programa ya lo usa
  — pasó en Windows con un servicio del sistema): cambiar `POSTGRES_PORT` / `PANEL_PORT` en `.env`,
  o solo para una corrida: `PANEL_PORT=8090 docker compose up`. El error dice "ports are not
  available ... Solo se permite un uso de cada dirección de socket".
- **Docker Desktop no arranca con doble clic** (Windows): probar `docker desktop start` desde la
  terminal, que además espera a que el motor responda.
