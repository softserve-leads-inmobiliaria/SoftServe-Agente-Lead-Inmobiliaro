# Pruebas y entorno local

## Levantar el stack base

Requiere Docker Desktop (Windows/Mac) o Docker Engine + Compose (Linux) **abierto y corriendo**.

```
docker compose up --build
```

No hace falta crear un `.env`: `docker-compose.yml` trae defaults de desarrollo. Copiar
`.env.example` a `.env` solo si se quiere cambiar algún puerto o credencial. Los ajustes del producto
(reintentos del LLM, *scheduler*, alertas, umbral de atención) están en `config/core.env` y
`config/panel.env` (DT-48); para cambiar uno solo en tu máquina, poné la misma variable en `.env`.

| Servicio | URL / puerto | Qué es |
| :--- | :--- | :--- |
| `postgres` | `127.0.0.1:5432` (usuario/clave/BD: ver `.env.example`; desde el host, mejor `127.0.0.1` que `localhost`, ver `db/settings.py`) | Postgres 16 con `pgvector` |
| `phoenix` | http://localhost:6006 (OTLP/HTTP `/v1/traces`, OTLP/gRPC en `4317`) | Observabilidad (DT-9) |
| `panel` | http://localhost:8080/panel/<token> (`/health` para el estado) | Panel de asesores: lista de leads de su zona y ficha con score y transcripción (B-5, DT-5) |
| `migrate` | — (termina al acabar) | Aplica `alembic upgrade head` (esquema completo, C-3) |
| `seed` | — (termina al acabar) | Siembra los 3 asesores de demo (B-5) y el inventario de 12 propiedades y 3 promociones (C-9), idempotente. Imprime la URL del panel de cada asesor y el resumen del catálogo |
| `catalog` | solo dentro de la red de Docker (`http://catalog:8000`, MCP en `/mcp`, agenda en `/scheduling`) | Catalog Service como servidor MCP sobre Postgres (C-1), más la agenda de asesores y visitas por HTTP (C-2, DT-32). El inventario de demo (C-9) lo siembra el servicio `seed` al levantar el stack; los tests que necesitan otras filas siembran las suyas |
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
2. `https://t.me/<bot>?start=prop_00231` → el saludo nombra la propiedad del anuncio (`ref. prop_00231`), el
   lead queda con la zona de esa propiedad y, con Bedrock, "¿sigue disponible?" se responde sobre ella sin
   pedir la referencia (#122).
3. `STOP` y `/stop` → `consent_status=withdrawn` y `status=closed_lost` en `leads`, sin pasar por el LLM.
4. Un segundo `getUpdates` externo devuelve `409 Conflict`: confirma que el *polling* corre de verdad.

### Ver el panel de asesores (B-5)

`docker compose up -d --build` ejecuta `seed`, que imprime una línea por asesor con su URL:
`docker compose logs seed` → `adv_001  Valeria Ortiz  Providencia  /panel/<token>`. Abrir
`http://localhost:8080/panel/<token>`. El panel filtra por `Lead.zone`, que el core rellena con la zona de la
primera propiedad que el lead consulta (#81; el ruteo por zona al asesor es A-10). Para verlo con datos sin
pasar por una búsqueda, asignar la zona a mano (`update leads set zone='Providencia' where ...`). Un token
inventado y un lead de otra zona dan el mismo 404.

La vista de inventario y negocio (N-8) está en `http://localhost:8080/panel/<token>/negocio`, enlazada desde
el funnel. Sin cierres ni historial de estados cada sección dice "sin datos": para verla con datos hay que
sembrar la historia sintética de N-9 (ver "Historia sintética para la demo") y marcar "Incluir historia de
demo".

Tiempo de atención (B-12): un lead HOT que calificó hace más de `SLA_HOT_MINUTES` (30 por defecto) y que
nadie marcó "en gestión" sale resaltado en la cola, con cuánto lleva esperando. Para verlo sin esperar,
poner `SLA_HOT_MINUTES=1` en `.env` y reiniciar el panel. La mediana por asesor aparece en la vista de
negocio una vez que alguien toma un lead calificado, o con la historia de N-9 y "Incluir historia de demo".

### Avisos al asesor: en el panel, y por Telegram si lo registró (B-7, A-12, B-15)

El asesor recibe dos avisos, cada uno una sola vez por lead y en el turno exacto de la transición. Los dos
quedan siempre en su panel, en `http://localhost:8080/panel/<token>/avisos`, con el contador de no leídos.
La página se recarga sola cada 30 segundos, y abrir un aviso lo marca como leído y lleva a la ficha
(B-15, DT-42). No hace falta registrar ningún asesor en Telegram: con una sola cuenta, la del lead, se
prueba todo.

Telegram es un canal adicional. Con `TELEGRAM_BOT_TOKEN` puesto, un asesor se registra una vez abriendo su
enlace `https://t.me/<bot>?start=adv_<token>` (el `<token>` es el de su URL del panel:
`docker compose logs seed`). El bot responde "te voy a avisar…" y guarda su chat en
`advisors.telegram_chat_id`; un enlace inválido no registra a nadie y no dice por qué. Registrado, recibe
además cada aviso en el chat.

- **Lead calificado (B-7, DT-24):** la banda del score cruza a WARM/HOT. Tarjeta con score, zona, propiedad
  de interés y visita. El lead queda asignado al asesor que elige la agenda: el de su zona, o cualquiera
  si no hay (A-10, DT-42).
- **Traspaso (A-12, DT-28):** la conversación llega a `HANDOFF` (visita agendada) o `HUMAN_HANDOFF` (pidió una
  persona, consulta fuera de alcance o frustración). Ficha con el motivo, el score, la propiedad, la visita y
  la **transcripción completa** (en varios mensajes si excede el tope de Telegram). Va al mismo asesor que
  ya tenía el lead; si el mismo turno califica y traspasa, llega solo la ficha.

Para verlo: escribirle al bot como lead y pedir "quiero hablar con un asesor", y abrir los avisos del
asesor de la zona en el panel. En el panel van el resumen y el motivo, sin la transcripción, que está en la
ficha. Si el envío por Telegram falla, el aviso queda igual en el panel.

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
recibe `ThrottlingException` de Bedrock tras pocas llamadas seguidas (el limitador local no lo evita): los
errores transitorios (throttling, servicio no disponible, timeout, error interno, fallos de conexión) se
reintentan con espera exponencial y *jitter* (A-14, DT-30; límites en `LLM_RETRY_*`, ver `config/core.env`).
Cada reintento y el fallo definitivo quedan en el log como `warning` con el código del error y el número
de intento, y el fallo definitivo también en la traza del turno. Ojo: con `LLM_RATE_LIMIT_PER_MINUTE=10`
(igual a la cuota de Bedrock) el limitador local corta las ráfagas antes de que Bedrock haga *throttling*
y esos turnos caen al texto de respaldo sin reintentar; para que las ráfagas las absorba el reintento hay
que subir ese límite por encima de la cuota real (medición en el DT-30). Con el LLM real aplican los
guardrails de salida de factualidad (A-5), dirección reservada (A-6) y promesas no autorizadas (A-7); el
estado de cada uno está en `docs/DESIGN/guardrails.md` §3. Sigue pendiente fair housing (A-11), así que no
usarlo con leads reales sin revisar ese punto.

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

Esto es el esqueleto end-to-end de CT-4: arnés → core → catálogo por MCP (sobre Postgres desde C-1) →
respuesta. El agente es el *fake* por defecto. Desde C-9 el catálogo del stack sí trae el inventario
de demo, así que una búsqueda a mano devuelve propiedades; el arnés comprueba que los servicios se
hablan, no el inventario. Para probar un mensaje a mano con el stack arriba:

```
curl -X POST localhost:8000/events -H "Content-Type: application/json" -d @contracts/examples/event.valid.json
```

Para apagar: `docker compose down`. Para borrar también los datos: `docker compose down -v`.

### Demo en vivo (Telegram + Bedrock)

`scripts/demo/` reúne los pasos de una demo, que a mano son varios y en un orden que importa. Con el
`.env` de Bedrock y `TELEGRAM_BOT_TOKEN` puestos:

```
bash scripts/demo/up.sh       # arranca Docker si hace falta, levanta core, panel y phoenix, corre el seed y comprueba
bash scripts/demo/down.sh     # exporta las conversaciones a txt y apaga borrando la base (--purge: también las imágenes)
```

- `up.sh` no levanta el arnés: sus guiones esperan el agente *fake* y con Bedrock fallan y gastan cupo.
  Tampoco crea leads de prueba, así que la cola del panel arranca vacía. Sí siembra la historia sintética
  de N-9, que solo se ve en la vista de negocio. Al final imprime la URL del panel de cada asesor
  (cambia en cada `seed`), la de la vista de negocio con la historia y el enlace del anuncio para
  empezar como lead. También el enlace de Telegram de cada asesor, que es opcional: los avisos quedan en
  el panel y el enlace solo agrega una copia al chat (DT-42).
- `python scripts/demo/conversar.py [escenario ...]`: ensayo automático por `POST /events` contra el LLM
  del `.env` (deep-link hasta el asesor, búsqueda y recomendación, guardrails), con pausas para no pasar
  el cupo de 10 llamadas por minuto. Los avisos al asesor no salen por esta vía: solo por Telegram.
- `python scripts/demo/exportar.py [minutos]`: guarda en txt las conversaciones activas en ese lapso
  (por defecto 60), con el estado final de cada una. `down.sh` lo corre solo antes de borrar la base.

Las transcripciones quedan en `tmp/demo/transcripciones/`, que no se versiona. Llevan el texto que guardó
`core`, ya sin PII ni categorías protegidas; el aviso de privacidad no aparece porque lo agrega el canal.

### Corrida real contra los 3 servicios (OBS-6)

`docker compose run --rm harness` solo mira las respuestas de `core`. La corrida real además comprueba
lo que quedó en los servicios: el estado final de la FSM en Postgres y, para cada lead con zona, que
aparezca en la cola del asesor de esa zona en el `panel` con su etapa y que su ficha muestre la
transcripción. No usa nada simulado: arnés → `core` → `catalog` por MCP → Postgres → `panel` (DT-43).

```
docker compose up -d --build core panel phoenix
docker compose run --rm seed                  # asesores e inventario: sin asesores el panel no se comprueba
docker compose run --rm --build harness       # 8/8 dentro de los contenedores
python harness/scripts/live_run.py            # 8/8 contra los 3 servicios, desde el host
```

- Cada corrida usa ids nuevos (`run_id`), así que se puede repetir sobre la misma base sin heredar
  conversaciones que quedaron en `HUMAN_HANDOFF`. Los leads de la corrida quedan en la base: en la cola
  del panel aparecen junto a los de una demo.
- El informe queda en `tmp/harness/<fecha>_corrida_real.md`, con la tabla de resultados y la
  transcripción de cada guion. Es la evidencia que se adjunta al PR.
- Con `LLM_PROVIDER=bedrock`: `python harness/scripts/live_run.py --sin-frases`. El modelo responde bien
  con otras palabras, así que no se exigen las frases fijas de los guiones, pero sí el contrato, que la
  respuesta no llegue vacía, el estado final y el panel. Cada turno espera hasta `HARNESS_HTTP_TIMEOUT`
  segundos (60 por defecto: con Bedrock un turno puede esperar cupo y reintentos).
- **Con Bedrock, dar más espera de cupo a `core`.** La corrida manda turnos seguidos de 2 o 3 llamadas, y
  con el límite local por defecto (10 por minuto, 15 s de espera, DT-19) el cupo se agota desde el segundo
  guion: los turnos responden con el texto de respaldo y dejan un `warning` `LocalRateLimit` en el log.
  Sin subir el límite, alcanza con dejar esperar más a cada turno (la corrida tarda unos 6 minutos):

  ```
  LLM_MAX_QUEUE_WAIT_SECONDS=60 docker compose up -d --wait core
  HARNESS_HTTP_TIMEOUT=120 python harness/scripts/live_run.py --sin-frases
  docker compose up -d --wait core          # al terminar, vuelve a los valores del .env
  ```

  Los estados finales esperados son los del agente *fake*. Con Bedrock, el modelo suele pedir la zona
  antes de buscar: `saludo_y_busqueda` y `deep_link_solo_precio` pueden terminar en `QUALIFY`, y
  `flujo_completo_hasta_handoff` en `SCHEDULE` (pide la visita sin haber visto una propiedad y queda
  esperando su horario preferido). Con `--sin-frases` esos estados también se aceptan
  (`ALSO_VALID_WITH_LLM` en `harness/scripts/expectations.py`), y el panel se comprueba con el estado al que
  llegó. El modelo no siempre elige igual, así que dos corridas pueden diferir. Corridas del 3-oct: ningún
  turno al texto de respaldo (#48).
- Si el `panel` se publica en otro puerto (`PANEL_PORT` en el `.env`), pasarlo con `PANEL_URL`, por
  ejemplo `PANEL_URL=http://127.0.0.1:8081`.
- `CORE_URL`, `PANEL_URL` y `DATABASE_URL` (o `POSTGRES_*`) cambian a dónde apunta. Por defecto, al stack
  de `docker compose`.

**La parte de Telegram es manual.** Un bot no puede mandarse mensajes a sí mismo, así que alguien escribe
desde su Telegram con `core` levantado con `TELEGRAM_BOT_TOKEN`, y después se verifica en la base y el
panel:

1. `https://t.me/<bot>?start=prop_00231` → saludo con aviso de privacidad y la propiedad del anuncio.
2. "¿Cuánto cuesta?" → responde sobre esa propiedad.
3. "Quiero hablar con un asesor" → `HUMAN_HANDOFF` en `conversations`; el lead aparece en el panel de
   Providencia con la transcripción.
4. `STOP` → `consent_status=withdrawn` en `leads` y una fila `input.opt_out` en `audit_log`.

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
python -m pytest tests/harness         # arnés + e2e: levanta catalog, core y panel como procesos, sin Docker
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
mismo resultado ya revalidado. Los tests que pasan por `process_turn` usan `services/core/tests/catalog_stub.py`, un
catálogo con `id`, `status` y `get_availability`. Para verlo en vivo: en la traza de un turno con
búsqueda aparecen `catalog.get_availability` además de `catalog.search_properties`.

### Factualidad de la respuesta (A-5)

`services/core/app/factuality.py` compara todo número/estado de la respuesta candidata contra
`last_tool_results` de ese turno (más lo que el propio lead dijo). Solo aplica al agente real
(`BedrockAgent`): el falso construye su respuesta directo de los resultados, sin margen para inventar.

- `services/core/tests/test_factuality.py`: la regla en sí (extracción de números y estados, qué
  cuenta como literal — campos numéricos, números dentro de una nota, tamaño de `properties`).
- `services/core/tests/test_agent.py` (sección A-5): la integración completa — regenerar con la
  instrucción explícita, degradar tras el segundo fallo, fusión de `tool_results` cuando la
  regeneración vuelve a llamar una tool, y qué pasa si Bedrock falla durante la regeneración.
- `services/core/tests/test_factuality_gold_set.py`: el gold set de la historia (≥30 casos, DoD "100%
  en CI"). No llama a ningún LLM: prueba `factuality.check()` contra pares fijos de (respuesta,
  `tool_results`), así que corre sin credenciales. Un caso nuevo (una redacción real que rompió el
  guardrail) se agrega ahí, nunca se borra uno viejo salvo que deje de aplicar.
- Para verlo en vivo con Bedrock real: forzar una respuesta con un número fuera de lo que devolvió la
  tool es difícil de reproducir a propósito (el modelo no suele hacerlo); lo verificable en vivo es que
  `AuditLog` nunca tiene entradas `output.factuality_*` en una sesión normal, y sí las tiene si el
  modelo alucina (ver DT-23 para el detalle de qué se audita).

### Factualidad sobre el catálogo de demo (C-7)

`test_factuality_gold_set.py` (A-5) congela dicts escritos a mano. `test_factuality_catalogo_gold_set.py`
(C-7) hace lo mismo sobre la **salida real** del catálogo de demo: sus fixtures las produce
`services/core/tests/demo_catalogo.py`, que corre `CatalogService` de verdad contra la base migrada que ya
monta el `conftest.py` de `core`, sembrada con `scripts/seed_catalog.seed()` — el mismo del compose, así que
el inventario no está duplicado en ningún lado. **75 casos** (46 que pasan, 29 que bloquean) sobre **27
turnos**, que cubren las 12 propiedades del inventario y las 5 tools. No llama a ningún LLM.

Es el complemento de A-5, no su reemplazo: los 32 casos de A-5 no se tocan (prueban un criterio que sigue
vivo, DT-23, y la política del repo es no borrar casos antiguos). Lo que C-7 agrega es que las fixtures
**dejan de estar escritas a mano** y, con ellas, el gate que faltaba.

- `test_la_salida_cruda_del_catalogo_cumple_el_contrato`: valida la salida de cada turno contra
  `contracts/mcp/<tool>.schema.json` (27/27 en verde). **Este es el test nuevo**: los dicts a mano de A-5
  nunca se contrastaron con el contrato, y cuando C-1 los dejó atrás —les faltaba `address_approx`,
  `type`, `zone`, `address_exact` y `valid_until`— nada lo señaló. Si el catálogo deja de devolver un campo,
  o lo devuelve con otro tipo, el CI se cae.
- `test_a4_solo_agrega_esos_dos_campos_y_no_toca_el_resto`: el `outputSchema` tiene
  `additionalProperties: false` y no contempla la revalidación, así que la forma post-A-4 no se valida contra
  el contrato sino que se fija explícitamente: `search_properties` es el mismo objeto más
  `availability_checked_at` y `unavailable` (con solo `id`, `title` y `status`), `get_property` más
  `availability_checked_at`, y las otras tres tools pasan intactas. Además comprueba que el estado y el
  timestamp que,A-4 estampa son los de `get_availability` y no los del listado.
- `test_gold_set_is_100_percent_green`: el gate de los 75 casos.

**El catálogo corre en un subproceso.** `core` y `catalog` comparten el nombre de paquete `app` (los dos
`conftest.py` lo ponen en `sys.path`, y CI corre cada carpeta en un proceso distinto por eso), así que el
generador se lanza con `PYTHONPATH=services/catalog` y devuelve el JSON por stdout. Ver DT-36 para por qué las
fixtures se generan en cada corrida en vez de commitarse.

**La capa de A-4 la corre el guardrail real, no una copia.** `AvailabilityGuard` no habla con el catálogo por
MCP: solo necesita un objeto con `await call(tool, arguments)`. `demo_catalogo._TurnoGrabado` se lo da
reproduciendo las salidas de la corrida, incluida la de `get_availability` de cada propiedad. Por eso el
`last_tool_results` que come el guardrail es el de producción, y si `availability.py` cambia la forma del
payload, el gold set se cae con él.

**El reloj va fijo en los dos lados, y es por una razón concreta.** `factuality._walk` saca números de
*cualquier* string del payload, así que el `checked_at` real del catálogo
(`2026-09-28T11:59:35.824340-05:00`) metía `35.824340` en el set de números autorizados — y los
microsegundos cambian en cada corrida, así que un caso que rozara ese número haría fallar el gold set al
azar. El generador inyecta `now` fijo a `CatalogService` (el servicio ya recibe el reloj por inyección), con
lo cual A-4 estampa `2026-01-01T00:00:00+00:00`, el mismo valor estable de `catalog_stub.py`. El test de A-4
lo comprueba, así que si el reloj dejara de inyectarse el CI se cae en vez de volver el gold set aleatorio.

**Cómo se verifica que el gate muerde.** Por mutación: se rompe `factuality.py` a propósito y el gold set
tiene que caerse. La primera pasada detectó 7 de 8 roturas — faltaban los casos de *estado* no respaldado
("apartado"/"cerrada" cuando el turno no lo autoriza), porque los que había solo afirmaban estados correctos y
pasaban igual con el chequeo de estados desactivado. Con esos casos agregados se detectan las ocho. Para
agregar un caso que no sirva, hay que comprobar que rompe algo: **un gold set que no se cae al romper el
guardrail no está probando el guardrail.**

**Los límites de DT-23 quedan fijados como casos que pasan**, con el porqué al lado, no como `xfail`: el
guardrail no está roto, el límite es conocido y la repo no mergea en rojo. Los tres que aparecieron al
escribirlo: el precio de una propiedad apartada (A-4 la mueve a `unavailable` guardando solo
`{id, title, status}` y descarta el precio a propósito, así que cotizarla es inventar), un número que coincide
con el conteo del listado (`len(properties)`, que A-5 permite a propósito), y un número real de *otra*
propiedad del turno. Al escribir sobre datos reales esto aparece antes que con fixtures a mano, porque una
búsqueda devuelve varias propiedades.

### Fair housing en el scoring (C-6)

`services/core/tests/test_fair_housing_gold_set.py` prueba que el score no depende de una categoría
protegida. Son **27 pares de leads** idénticos salvo la mención, y corre el pipeline completo de un
turno — `inspect_input().signal_text` → el extractor de señales → `score_turn()`— porque el sesgo
puede entrar por cualquiera de las tres capas y probar solo la suma no encuentra nada. No llama a
ningún LLM: el extractor es el determinista de `fakes/fake_llm.py`, así que corre en milisegundos y
sin credenciales, mismo criterio que los gold sets de A-5 y A-6.

El *gate* es `test_el_score_no_se_mueve_entre_el_par`: compara total, **las cinco dimensiones** y la
banda, no solo el total, para que dos fugas que se compensen no se cuelen por el mismo número. La banda
importa porque ordena la cola del panel (`B-6`) y porque por debajo de `COLD` 41 el lead deja de
calificar y no llega a un asesor (`B-7`).

**Los 27 pares están en verde, sin `xfail`.** Cuando C-6 se escribió, los 6 de "con quién compras"
daban −8 (`pareja`), −12 (`familia`) y −20 (`con mis hijos`, que el guardrail borraba y dejaba el lead en
`no_declarado`). Los arregló `A-11` (DT-38), y #119 (DT-40) colapsó el enum para que el contrato no
guarde con quién se decide:

| Lo que dice el lead | Lo que ve el LLM | `decision_maker` | Authority |
| :--- | :--- | :--- | ---: |
| decido yo | igual | `declarado` | 20 |
| con mi pareja / con mis padres / con mi familia | igual | `declarado` | 20 |
| con mi esposa / con mi esposo / con mi marido / con mi novia | "con mi pareja" | `declarado` | 20 |
| con mis hijos | "con mi familia" | `declarado` | 20 |

Ojo con la banda: el control da 100 (`HOT`) y el corte es `WARM` ≤ 70, así que la aserción de banda del
gate es una red contra fugas de 30 puntos o más. Una fuga más chica se ve en el total y en la
dimensión, no en la banda.

Si un arreglo futuro deja un par pendiente, va con `xfail(strict=True)` y su razón: cuando se arregle,
el xfail se vuelve XPASS y el test se pone rojo solo, obligando a quitar la marca.

`SIN_DETECTAR` guarda las menciones que el guardrail de entrada todavía no marca: el score da 0 igual,
pero la mención llegaría intacta al LLM. Hoy está vacío. Hasta #116 tenía el par `colombiano`, porque
`input_guard._PROTECTED` solo listaba sustantivos (`nacionalidad`, `extranjeros`, …). Ahora también
detecta gentilicios y frases de procedencia, y el gold set suma los pares `venezolana`, `de_colombia` y
`de_venezuela`. Los falsos positivos (zonas, calles, monedas, idioma) los fija `test_input_guard.py`.

Para agregar un par se edita `MENCIONES` o `SITUACIONES` en el test; nunca se borra uno viejo salvo que
deje de aplicar.

### El registro de gold sets (OBS-4)

`services/core/tests/test_gold_sets_gate.py` es el lugar donde se ve, de una vez, **qué gold set hay y qué
mínimo le pide su historia**. Antes cada archivo traía su `assert len(CASES) >= N` a mano: eso ya ponía el
CI en rojo si un gold set bajaba de su umbral, pero los números estaban sueltos y tres de cuatro no
coincidían con lo que decía su historia (C-6, C-8 y A-7 no definían ninguno; los `15`/`20` los había
inventado quien escribió el test).

El registro declara `GOLD_SETS` con `historia → módulo → atributo → mínimo` y comprueba tres cosas:

| Test | Qué se cae |
| :--- | :--- |
| `test_umbral_de_cada_gold_set` | Un gold set con menos casos de los que su historia pide |
| `test_todo_gold_set_del_repo_esta_registrado` | Un `*gold*.py` nuevo que nadie anotó en el registro |
| `test_el_gate_detecta_un_gold_set_bajo_su_umbral` | El gate mismo, si dejara de detectar algo |

Ese último es el importante: un gate que no muerde no es un gate, así que el registro se autocomprueba
pasándose un mínimo imposible y exigiendo que lo atrape.

Para agregar un gold set hay que hacer dos cosas: crear el archivo en `services/core/tests/` y añadir su
entrada al registro con el mínimo que pida su historia. Si el archivo se llama de otra forma y no es un
`*gold*.py`, la segunda regla no lo encuentra — por eso la lista explícita es la fuente y esa regla es la
red.

Un gold set cuyo PR todavía no está en `main` se registra con `requerido=False`: el gate avisa con un
warning en vez de ponerse rojo, y cuando llega empieza a exigir su umbral. Hoy todos los registrados
están en `main`, así que ninguno lo usa. Ver DT-37.

### Casos borde del inventario de demo (C-9)

El catálogo del stack no arranca vacío: el servicio `seed` baja a Postgres las 12 propiedades y 3
promociones de `services/catalog/app/data.py` (`scripts/seed_catalog.py`). Los datos son ficticios y
estables, y ese archivo es la única fuente: la base, los tests y el arnés leen las mismas filas. Los
asesores los siembra `scripts/seed_advisors.py` (B-5), que todavía mantiene su propia copia.

El inventario lleva casos borde **a propósito**, para que la demo y los guardrails tengan algo real que
ejercitar y no solo el camino feliz:

| Caso | Dónde | Qué tiene que pasar |
| :--- | :--- | :--- |
| Propiedad en `apartado` | `prop_00234`, `prop_00236` | Se ofrece pero no se promete: no se puede afirmar que está disponible |
| Propiedad `cerrado` | `prop_00239` | No se ofrece como opción viva |
| Sin promociones | `prop_00235` | El agente no debe inventar una promoción que no existe |
| Promoción vencida | `promo_003` (`valid_until: 2000-01-01`) | **Nunca se devuelve** al cliente, aunque su propiedad sí se ofrezca |
| Sin recámaras: terreno | `prop_00241` | `bedrooms: 0` es un dato real, no uno faltante — y no tiene amenidades, así que tampoco se le atribuyen |
| Sin recámaras: local comercial | `prop_00242` | `bedrooms: 0` con `type: renta`; los requisitos de renta igual aplican |

Las amenidades de estas 12 propiedades están dentro del vocabulario cerrado de `_AMENITY_SYNONYMS`
(`services/core/app/promise_guard.py`). Si alguna saliera de ahí, el guardrail la marcaría como
alucinación sobre una propiedad que sí la tiene: al agregar una propiedad, agregar la amenidad al
vocabulario en el mismo PR.

`prop_00240` (estudio) también tiene `bedrooms: 0`, así que son tres propiedades sin recámaras, no dos.

### Historia sintética para la demo (N-9)

`scripts/seed_history.py` siembra seis meses, hasta ayer, sobre el inventario de C-9: el historial de
estados de cada propiedad, 6 cierres, unos 170 leads (con su puntaje, su toma por el asesor y las
propiedades que buscaron, consultaron o visitaron) y 90 búsquedas sin resultado. Necesita los asesores y
el inventario, así que va después del servicio `seed`:

```
docker compose run --rm seed                                         # asesores e inventario
docker compose run --rm seed python scripts/seed_history.py          # siembra, o vuelve a sembrar
docker compose run --rm seed python scripts/seed_history.py --borrar # la borra sin tocar datos reales
```

- Todo lleva `synthetic = true`. Solo lo ven la vista de negocio con "Incluir historia de demo"
  (`/panel/<token>/negocio?demo=1`) y el reporte semanal con `WEEKLY_REPORT_INCLUDE_SYNTHETIC=1`. La
  cola, la ficha, el funnel, el reparto de leads y las alertas no lo ven (DT-49).
- No cambia el inventario: el historial de cada propiedad termina en el estado que ya tiene. Una propiedad
  con historial real, o en otro estado, no se toca, y el script lo dice.
- Las fechas se cuentan hacia atrás desde hoy y cada corrida reemplaza la anterior, así que conviene
  volver a sembrarla el día de la demo. `--hoy AAAA-MM-DD` fija el día. `up.sh` ya lo hace.
- Los leads no tienen teléfono ni correo, su `telegram_user_id` empieza con `synthetic-` y no tienen
  `funnel_events`, así que se pueden borrar.

Qué se ve en la vista de negocio, con el periodo de los últimos seis meses:

| Caso (propuestas.md §2.2) | Dónde |
| :--- | :--- |
| Zonas que rotan rápido | Rentas de Chapalita y Providencia: se rentan en menos de dos semanas |
| Zonas que rotan lento | Tlaquepaque (venta) y Andares (preventa): meses en el mercado |
| Estancada con consultas y sin visitas | `prop_00231`: se cayó el apartado y lleva más de 90 días disponible |
| Estancada sin consultas | `prop_00232` (más de 30 días) y `prop_00233` (más de 90) |
| Cierres atribuibles y no atribuibles al bot | 4 de los 6 cierres: el lead consultó o visitó esa propiedad con el bot |
| Tiempo de atención por asesor (B-12) | Valeria toma en minutos, Camila en media hora y Andrés en horas |
| Búsquedas sin resultado | Renta de 3 recámaras en Providencia, la más pedida ("providencia" se junta con "Providencia") |

La última semana tiene al menos un cierre (una unidad de la preventa de Andares).

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

### Imágenes endurecidas (#65)

Las imágenes propias (`core`, `catalog`, `panel`, `harness` y la de `db`, que usan `migrate` y `seed`)
parten de la misma base con versión exacta, corren con un usuario sin privilegios (`app`, uid 10001) y
no llevan tests. `core`, `catalog` y `panel` traen su `HEALTHCHECK` en la imagen; el compose no lo
repite. `tests/infra/test_dockerfiles.py` lo comprueba leyendo los Dockerfiles, porque CI no construye
las imágenes. Para verlo en las imágenes construidas:

```
docker compose run --rm --build harness
docker compose exec core id
```

`id` tiene que devolver `uid=10001(app)`, nunca `uid=0(root)`.

Además, el compose corre esos contenedores sin capacidades del kernel (`cap_drop: [ALL]`), sin poder
ganar privilegios (`no-new-privileges`) y con el sistema de archivos de solo lectura, salvo `/tmp`
(#104). Si un servicio empieza a escribir en disco va a fallar con `Read-only file system`: necesita
escribir en `/tmp` o un volumen propio.

Los puertos se publican solo en `127.0.0.1` (`BIND_ADDRESS`): desde otro equipo de la red no se llega a la
base, al core ni al panel. Para abrir el panel desde un celular en una demo, poner `BIND_ADDRESS=0.0.0.0`
en `.env`, sabiendo que eso también expone Postgres con la contraseña de desarrollo.

## Caveats de Docker conocidos

Se agregan aquí apenas se descubren.

- **"failed to connect to the docker API ... dockerDesktopLinuxEngine"** (Windows): Docker Desktop
  no está corriendo. Abrirlo y esperar a que diga "Engine running" antes de `docker compose up`.
- **Puerto ocupado** (p. ej. `5432` si hay un Postgres local, o `8080` si otro programa ya lo usa
  — pasó en Windows con un servicio del sistema): cambiar `POSTGRES_PORT` / `PANEL_PORT` en `.env`,
  o solo para una corrida: `PANEL_PORT=8090 docker compose up`. El error dice "ports are not
  available ... Solo se permite un uso de cada dirección de socket" o "... Intento de acceso a un
  socket no permitido por sus permisos de acceso".
- **Docker Desktop no arranca con doble clic** (Windows): probar `docker desktop start` desde la
  terminal, que además espera a que el motor responda.
