# Decisiones de arquitectura

Bitácora técnica de *por qué* — contexto, alternativas consideradas, consecuencias. El *qué* de
cada historia vive en `docs/STORIES/`; el *estado* (hecha/pendiente) vive solo en GitHub
Issues/Project, nunca acá. Cada entrada enlaza al Issue/PR correspondiente cuando exista.

Formato de cada entrada: Contexto → Decisión → Alternativas → Consecuencias.

---

## DT-1 — Postgres como fuente de verdad del estado desde el día 1

**Estado:** aceptada

**Contexto.** El estado de una conversación (`FsmState`, historial, score) necesita sobrevivir
reinicios del servicio y ser consultable por el panel de asesores (`services/panel/`), no solo por
`core`. Definir esto recién cuando la primera historia de negocio lo necesite deja una ventana
donde el contrato ya está congelado (`CT-3`) pero la implementación real todavía vive en memoria.

**Decisión.** El estado se lee y escribe directo en Postgres desde `A-1` (la primera historia que
lo necesita) — no existe una versión "en memoria" que se persista después. `CT-3` declara el
contrato asumiendo esto explícitamente.

**Alternativas.** Estado en memoria con persistencia agregada más adelante — descartada: crea una
migración de arquitectura a mitad de proyecto en vez de una decisión tomada una sola vez.

**Consecuencias.** Toda historia que lea/escriba `FsmState`/`LeadScore`/`Conversation` (`A-1`,
`A-9`, `B-5`, `OBS-2`) depende de que `C-3` (migraciones) esté cerrada primero — ver el orden de
`docs/STORIES/ROADMAP.md`.

---

## DT-2 — Transporte del servidor MCP del Catalog Service: streamable HTTP, no `stdio`

**Estado:** aceptada

**Contexto.** `catalog` y `core` corren en contenedores Docker separados, comunicándose por red.
El transporte `stdio` de MCP asume que el cliente lanza al servidor como proceso hijo y habla por
sus *pipes* — incompatible con dos contenedores independientes.

**Decisión.** Exponer las tools `catalog.*` vía **streamable HTTP**, montado en `/mcp` dentro del
mismo FastAPI de `catalog` (junto a `/health`). `core` se conecta como cliente de red, apuntando a
`${CATALOG_SERVICE_URL}/mcp`.

**Alternativas.** `stdio` — descartado, incompatible con contenedores separados. SSE transport —
predecesor de streamable HTTP en el SDK de MCP; streamable HTTP es la opción vigente.

**Consecuencias.** Montar un sub-app de MCP en FastAPI/Starlette con `app.mount(...)` no propaga
su *lifespan* automáticamente — hay que arrancar el *session manager* del servidor MCP
explícitamente en el `lifespan` de `catalog`, o cada llamada a `/mcp` falla en tiempo de ejecución
(no se detecta con tests que invoquen las tools en proceso, solo probando la llamada real por
red). Al fijar la dependencia del SDK de `mcp` en `requirements.txt`, pinnear la versión exacta
(no un rango) — la API de alto nivel del SDK cambia entre versiones mayores.

---

## DT-3 — Jobs y colas: *scheduler* embebido en el proceso, sin Redis

**Estado:** aceptada

**Contexto.** El sistema necesita programar tareas (recordatorios, *nurturing* de leads fríos). El
volumen esperado es bajo.

**Decisión.** Un *scheduler* corriendo en el mismo proceso de FastAPI (embebido en su `lifespan`),
sin un *broker* externo. El estado del funnel vive enteramente en Postgres — no hace falta una
cola distribuida.

**Alternativas.** Redis + una cola dedicada — el patrón "de producción" clásico, pero añade un
contenedor y un servicio más para un volumen de jobs que no lo justifica a esta escala.

**Consecuencias.** Redis no forma parte del stack en ningún otro punto del sistema.

---

## DT-4 — Despliegue 100% local — Telegram vía *long polling*

**Estado:** aceptada

**Contexto.** El requisito es que todo el sistema levante con un solo `docker-compose up`. El modo
*webhook* de la Bot API de Telegram necesita una URL HTTPS alcanzable desde fuera, lo que empuja a
desplegar algo público.

**Decisión.** Telegram se integra con **long polling** (el bot pregunta activamente por mensajes
nuevos) — elimina la necesidad de URL pública, dominio, certificado TLS o túnel. Funciona detrás
de cualquier NAT/firewall.

**Alternativas.** Webhook + túnel (ngrok/Cloudflare Tunnel) — añade una pieza más que puede fallar
durante una demo en vivo. Webhook + VM en la nube — descartado, no aporta nada que *polling* no
resuelva más simple y gratis, y suma carga operativa de infraestructura.

**Consecuencias.** Cero costo de *hosting*, cero superficie pública de ataque. El sistema solo
existe mientras alguien del equipo tiene el `docker-compose up` corriendo.

---

## DT-5 — Auth del panel de asesores: token estático por asesor

**Estado:** aceptada

**Contexto.** El panel necesita distinguir la cola de cada asesor sin exponer datos de uno a otro.
Son 3 asesores conocidos de antemano.

**Decisión.** Cada asesor recibe un token secreto estático (`Advisor.access_token`, generado al
sembrar los datos de demo) y accede por una URL del tipo `/panel/{advisor_token}` — sin login, sin
verificación por correo, sin proveedor OAuth.

**Alternativas.** *Magic link* por email — añade un proveedor de envío de correo para un beneficio
marginal con 3 usuarios fijos. OAuth2 — exige registrar una app OAuth; sobra para 3 asesores
conocidos.

**Consecuencias.** No es autenticación de nivel producción (el token no expira, no hay 2FA) —
aceptable porque el panel no expone datos fuera del dominio del propio asesor y el universo de
usuarios es fijo. Un token inválido o de otra zona responde **404 uniforme**, nunca 401/403, para
no filtrar información por el código de estado (ver `B-5`).

---

## DT-6 — Proveedor de LLM: AWS Bedrock (Claude Sonnet para diálogo, Haiku para clasificación)

**Estado:** aceptada

**Contexto.** El agente necesita un modelo conversacional para el diálogo con el lead y un modelo
más barato/rápido para clasificar intención y extraer señal estructurada por turno.

**Decisión.** Claude vía AWS Bedrock: el modelo más capaz para la conversación real
(`generate_reply`), un modelo más liviano para clasificación de intención y extracción de
`ScoreSignal` por turno (mismo patrón de llamada aparte, no mezclado con la respuesta visible al
lead). Límite de tasa a respetar: **10 solicitudes/minuto por modelo** — un *rate limiter* propio
con cola/backoff, sin Redis (ver DT-3), diseñado desde el principio y no como parche bajo carga.

**Alternativas.** Mezclar la extracción de señal en la misma llamada que genera la respuesta —
descartado: mezclaría una responsabilidad de "extracción estructurada silenciosa" con la
conversación visible del lead.

**Consecuencias.** Fijar el identificador exacto del modelo (`model_id`) en `.env.example` y
probarlo con una llamada real antes de darlo por bueno — algunos IDs de modelo en Bedrock
requieren un *inference profile* aparte, no siempre evidente hasta la primera llamada real. La
respuesta del LLM para señal estructurada puede venir envuelta en bloque de código Markdown pese a
pedir "solo JSON" en el *prompt* — parsear con tolerancia a eso (extraer el JSON del bloque) antes
de fallar.

---

## DT-7 — Embeddings de la FAQ: modelo autoalojado, no un proveedor cloud aparte

**Estado:** aceptada

**Contexto.** `C-5` (FAQ con `pgvector`) necesita un modelo de embeddings. Acoplar el proyecto a
un proveedor cloud distinto del elegido para el LLM (DT-6) suma una dependencia externa más sin
necesidad real a esta escala.

**Decisión.** Modelo de *sentence embeddings* autoalojado (corre en el propio contenedor, sin
llamada de red a un proveedor externo), dimensión fija consistente con la columna `vector(N)` de
`FaqEntry` (`design.md` §1).

**Alternativas.** Embeddings del mismo proveedor cloud que el LLM — descartado por ahora: acopla
el pipeline de FAQ a la disponibilidad/cuota de ese proveedor para algo que no lo necesita.

**Consecuencias.** `C-5` fija el nombre exacto del modelo y su dimensión al implementarse; ese
valor es el que define `N` en el esquema de `FaqEntry`.

---

## DT-8 — Versionado de los contratos: `schema_version` en el evento, archivo nuevo si es incompatible

**Estado:** aceptada

**Contexto.** El evento canónico (`CT-1`) lo consumen `core`, el arnés y los canales, construidos
en paralelo. Sin una regla previa, el primer cambio de forma se improvisa a mitad de proyecto.

**Decisión.** El evento lleva un campo obligatorio `schema_version` (hoy `1`) y vive en
`contracts/event.schema.json`. Un cambio *compatible* (agregar un campo opcional) se hace en el
mismo archivo, sin cambiar la versión. Un cambio *incompatible* (quitar o renombrar un campo,
endurecer un tipo, volver obligatorio un campo opcional) crea `contracts/event.v2.schema.json`
con `schema_version` 2; el archivo anterior se mantiene hasta que todos los consumidores migren.
No se usan carpetas por versión.

**Alternativas.** Carpeta `contracts/<versión>/` por cada versión — descartada: mueve todos los
contratos por un cambio en uno solo. Sin campo de versión en el evento — descartada: un consumidor
no podría distinguir en runtime qué forma recibió.

**Consecuencias.** Todo productor de eventos debe fijar `schema_version`. Los contratos de las
tools MCP (`CT-2`) y de estado de la FSM (`CT-3`) no llevan este campo por ahora; si alguno lo
necesita, se aplica la misma regla en una entrada nueva.

---

## DT-9 — Observabilidad: Arize Phoenix autoalojado (OpenTelemetry)

**Estado:** aceptada

**Contexto.** `OBS-1` necesita trazar cada turno (estado FSM, tools, guardrails, latencia, costo)
con auto-instrumentación del SDK del proveedor de LLM (DT-6) y *spans* manuales. El requisito de
`CT-6` es que todo levante con `docker compose up`, sin nube y sin Redis (DT-3, DT-4).

**Decisión.** Phoenix corriendo como un solo contenedor en el `docker-compose`, recibiendo trazas
OpenTelemetry por OTLP (HTTP en `6006`, gRPC en `4317`). Imagen con versión fija, persistencia en
un volumen propio.

**Alternativas.** Langfuse autoalojado — descartado: su versión vigente exige varios servicios
adicionales (incluido Redis), lo que contradice DT-3. Solo logs JSON — descartado: no da vista de
trazas por turno ni de costo por llamada al LLM.

**Consecuencias.** Las trazas usan el estándar OpenTelemetry, así que cambiar de herramienta no
obliga a re-instrumentar el código (esto es la base del plan B de `OBS-8`). Fijar la versión de la
imagen y actualizarla a propósito, no con `latest`.

---

## DT-10 — SDK de MCP: `mcp==1.30.0` fijo (serie 1.x), no 2.x

**Estado:** aceptada

**Contexto.** El catálogo (CT-8) y, más adelante, el cliente de `core` usan el SDK oficial de MCP por
streamable HTTP (DT-2). La serie 2.x renombró la API de alto nivel (`FastMCP` pasó a `MCPServer`) y
la mayoría de la documentación y ejemplos siguen siendo de 1.x.

**Decisión.** Fijar `mcp==1.30.0` (versión exacta, no un rango) en `services/catalog/requirements.txt`, y
la misma versión en cualquier servicio que use el SDK. Los argumentos de las tools se validan contra
el `inputSchema` de `contracts/mcp/`, así que el contrato queda como única fuente de verdad y no
depende de cómo el SDK tipa los parámetros.

**Alternativas.** `mcp` 2.x — descartado por ahora: API distinta a la documentada y sin ventaja
que el proyecto necesite. Sin fijar versión — descartado: un `pip install` futuro podría traer otra
API mayor.

**Consecuencias.** Subir a 2.x será una decisión propia, con migración del servidor y del cliente a la vez.

---

## DT-11 — Dónde vive el estado de la FSM: `Conversation.fsm_state` + `fsm_context` + `lead_scores`

**Estado:** aceptada

**Contexto.** El contrato `FsmState` (CT-3) junta el estado actual, el contexto del turno
(`lead_qualified`, `last_tool_results`, `score_signal`) y el último score. `design.md` §1 ya pone
`fsm_state` en `Conversation`; guardar además una copia en otra tabla dejaría dos fuentes de verdad
que pueden divergir (DT-1).

**Decisión.** El estado actual vive **solo** en `conversations.fsm_state` (con un `CHECK` contra la
lista de estados). El resto del contexto vive en `fsm_context` (1:1 con la conversación, columnas
`jsonb` para `last_tool_results` y `score_signal`) y el último score en `lead_scores` (una fila por
lead). `FsmState` se arma leyendo las tres. Migraciones con Alembic; los modelos de `db/models.py`
son la fuente de verdad y un test falla si las migraciones se desvían de ellos.

**Alternativas.** Una sola tabla `fsm_state` con todo — descartada: duplica `fsm_state` de
`Conversation`, que el panel (B-5) ya lee. Guardar el contexto como un único `jsonb` en
`Conversation` — descartada: mezcla el estado con datos que se sobrescriben cada turno.

**Consecuencias.** `A-1` lee y escribe estado con una transacción sobre `conversations` +
`fsm_context`. `funnel_events` es *append-only* (un *trigger* rechaza UPDATE/DELETE) y sin
`ON DELETE CASCADE`: un lead con eventos no se puede borrar; el opt-out marca
`consent_status=withdrawn` y no borra. La dimensión del vector de la FAQ (`EMBEDDING_DIM=384`) es un
valor de partida que `C-5` confirma o cambia con una migración (DT-7).

---

## DT-12 — FSM: transiciones por *triggers*, estados terminales y bloqueo por conversación

**Estado:** aceptada

**Contexto.** `A-1` implementa el diagrama de `design.md` §2. El diagrama dice que `HUMAN_HANDOFF` es
alcanzable "desde cualquier estado", pero solo dibuja la arista desde cinco; y no dice qué hace un
mensaje que llega a un estado terminal, ni qué pasa si dos mensajes de la misma conversación llegan a la vez.

**Decisión.**
- La FSM es una tabla `(estado, trigger) -> estado` (`services/core/app/fsm.py`); cualquier otra
  combinación lanza `InvalidTransition`. Los mensajes del lead se traducen a *triggers* con una
  política mínima basada en el fake LLM (`trigger_for`), que `A-2` y `A-9` reemplazan con señales reales.
- Terminales: `HANDOFF`, `HUMAN_HANDOFF` y `LOST`. Sin salida; los mensajes que llegan ahí se
  responden pero no mueven el estado.
- `HUMAN_HANDOFF` se puede alcanzar desde todos los estados no terminales, incluido `NURTURE`
  (una arista más que las dibujadas).
- Cada turno bloquea la fila de la conversación (`SELECT ... FOR UPDATE`) durante el
  leer-decidir-escribir: dos mensajes simultáneos se procesan uno tras otro. La respuesta (que puede
  esperar al catálogo por red) se arma **antes**, sin transacción abierta.
- Los triggers `no_response` (`DISCOVERY -> NURTURE -> LOST`) los emitirá el *scheduler* (fuera de `A-1`).

**Alternativas.** Estado en memoria — descartada por DT-1. Sostener la transacción durante toda la
llamada al catálogo — descartada: bloquea la fila mientras se espera por red.

**Consecuencias.** Si el core recibe un evento de un lead que aún no existe crea una fila `Lead`
provisional (`telegram_user_id` = id del lead): el canal (`B-3`) debe crear al lead con su id real
antes de que su primer evento llegue al core. La intención `otro` nunca dispara `HUMAN_HANDOFF`
hasta que `A-2`/`A-13` revisen el criterio.


## DT-13 — Cliente MCP del catálogo: descubrimiento de tools, sin nombres cableados

**Estado:** aceptada

**Contexto.** `core` debe usar el mismo cliente contra el catálogo falso (`CT-8`) y contra el real
(`C-1`), sin ramas de código por entorno (`A-3`). Si el cliente conociera los nombres o los esquemas
de las tools, cada cambio del catálogo obligaría a tocar `core`.

**Decisión.**
- `CatalogClient` (`services/core/app/catalog_client.py`) descubre las tools con `tools/list` en la
  primera conexión y las guarda en memoria (`ToolSpec`: nombre, descripción, esquema de entrada y de
  salida). No contiene nombres de tools ni distingue entornos: lo único que cambia es
  `CATALOG_SERVICE_URL`.
- `call` rechaza con `UnknownTool`, sin tocar la red, una tool que el catálogo no publica. Si el
  nombre no está en el caché, vuelve a descubrir una vez (el catálogo pudo publicar una tool después
  de que `core` arrancara) antes de rechazar.
- `tool_specs()` entrega lo descubierto (`name`, `description`, `input_schema`) para que `A-2`/`A-4`
  se lo pasen al LLM como tools disponibles.
- La validación de los argumentos sigue siendo del servidor (`catalog_error` con `INVALID_INPUT`); el
  cliente no la duplica.

**Alternativas.** Lista fija de las 5 tools en `core` — descartada: acopla `core` al catálogo.
Sesión MCP persistente entre llamadas — descartada por ahora: cada llamada abre su sesión y el coste
extra es un viaje de ida y vuelta local; se reevalúa si la latencia del turno lo exige.

**Consecuencias.** Que el catálogo publique exactamente las 5 tools de `contracts/mcp` se verifica en
las pruebas de extremo a extremo (`tests/harness`), no en el cliente. El caché no se invalida solo si
una tool desaparece; una llamada a ella fallará con el error del catálogo.


## DT-14 — Score BANT-E integrado en el turno: señal acumulada en Postgres

**Estado:** aceptada

**Contexto.** `C-4` dejó las reglas del score como función pura. `A-9` tiene que acumularlo turno a
turno y persistirlo desde el primer commit (mismo criterio que `A-1`), sin que el LLM emita el número.

**Decisión.**
- Cada turno, dentro de la misma transacción que mueve la FSM y bajo el mismo bloqueo de la
  conversación, se acumula la señal y se recalcula el score (`app/lead_scoring.py`); el estado y el
  score se guardan juntos o no se guarda nada.
- Reparto del contrato `ScoreSignal`: el LLM emite solo `budget_hint`, `decision_maker` y `urgency`;
  `matching_properties` (conteo de `search_properties`) y `meets_requirements` (`check_requirements`)
  los deriva el código de los resultados de las tools del turno.
- Acumulación: lo declarado se conserva; un campo que el mensaje no menciona, o que el LLM devuelve
  inválido, **no** pisa lo ya declarado (se ignora antes que inventar); lo declarado más
  recientemente gana. Los campos que no son del contrato (p. ej. una categoría protegida) no llegan a
  la señal.
- Se persisten la señal acumulada en `fsm_context.score_signal`, los resultados de tools del último
  turno en `fsm_context.last_tool_results` (se sobrescriben cada turno) y el score en `lead_scores`
  (una fila por lead, con *upsert*).
- Se puntúa en todos los estados, también en los terminales: el asesor ve el último score.
- El score no condiciona las transiciones de la FSM. La banda mínima para recomendar es de `A-4`.

**Alternativas.** Recalcular el score desde el historial completo cada turno — descartada: cuesta más
y obliga a guardar todo el texto. Puntuar fuera de la transacción de la FSM — descartada: podría
quedar un score sin el estado que lo produjo.

**Consecuencias.** Con el fake LLM la extracción es por palabras clave; el LLM real (`A-2`) debe
devolver el mismo formato de tres campos y `merge_signal` descarta lo que no cumpla. `check_requirements`
aún no se invoca en ningún turno, así que `eligibility` queda en 0 hasta que `A-4`/`A-12` lo llamen.
`decision_maker` distingue `pareja` y `familia`; su tratamiento frente a fair housing lo resuelven
`A-11`/`C-6`.


## DT-15 — Guardrail de entrada: reglas deterministas antes de la FSM y el LLM

**Estado:** aceptada

**Contexto.** `A-8` cubre los 5 detectores de `docs/DESIGN/guardrails.md` §1. El detalle de qué hace
cada acción al detectar, y qué pasa con un lead dado de baja, no estaba fijado.

**Decisión.**
- Los detectores son reglas (`services/core/app/input_guard.py`, función pura); ninguno usa LLM. El
  turno los aplica antes de responder (`app/turn.py`).
- **`/stop`** (comando o frases como "dame de baja", "no me escribas"): marca
  `leads.consent_status=withdrawn`, lo audita y confirma con un texto fijo. Manda sobre todo lo demás
  y no toca la FSM, el score, el catálogo ni el LLM. Un lead ya dado de baja recibe siempre un aviso
  fijo y ningún otro flujo. Una frase con negación ("no dejen de mandarme opciones") no cuenta.
- **Anti-*jailbreak*:** se contesta como una pregunta de dominio cualquiera (intención `otro`), sin
  reflejar la instrucción y sin efectos: no dispara traspaso a humano ni búsquedas, y no aporta señal
  al score. Se audita.
- **PII no solicitada** (tarjeta con dígito verificador válido, CURP, RFC, INE/pasaporte/licencia con su
  número): se sustituye por `[dato omitido]` antes de que el texto llegue a la FSM, el LLM o la base, y
  la respuesta recuerda qué datos sí hacen falta. La auditoría guarda el tipo, nunca el valor.
- **Fuera de dominio:** respuesta acotada, sin mover la FSM. Un dictamen legal, fiscal o crediticio se
  declina siempre; un tema ajeno solo si el mensaje no habla de propiedades. Al segundo mensaje fuera de
  dominio de la conversación la respuesta ofrece un asesor; la oferta no fuerza `HUMAN_HANDOFF`, lo
  pide el lead. El conteo sale de `audit_log`.
- **Categoría protegida:** el mensaje se responde con normalidad y se audita **sin** decir cuál. La señal
  del score se extrae de una copia del texto sin esos fragmentos, de modo que mencionarla no cambia el
  score: descartar solo el campo afectado lo habría bajado.

**Alternativas.** Clasificar con el LLM — descartada: no es determinista ni auditable, y `/stop` no puede
depender de un modelo. Bloquear el mensaje ante *jailbreak* o PII — descartada: la regla general es
degradar, no cortar. Contar el fuera de dominio en `fsm_context` — descartada: exigía una migración.

**Consecuencias.** Las listas de palabras del detector son una primera versión y se afinan con casos
reales; los falsos positivos se cubren en `services/core/tests/test_input_guard.py`. Tras la baja no hay
reingreso hasta que `B-2` defina el consentimiento. `decision_maker` sigue distinguiendo `pareja` y
`familia` en el contrato: su revisión frente a fair housing sigue en `A-11`/`C-6`.



## DT-16 — Traspaso a un humano: tres caminos y el criterio del cajón de sastre

**Estado:** aceptada

**Contexto.** `HUMAN_HANDOFF` debe ser alcanzable desde cualquier estado (`design.md` §2). `A-13` pide
tres caminos y que se revise, contra el criterio anotado en `A-2`, si la intención `otro` (cajón de
sastre del clasificador) es segura para disparar esa transición.

**Decisión.**
- **Petición explícita:** intención `solicitud_humano` (incluye "supervisor", "gerente", "hablar con una persona").
- **Fuera de alcance:** intención nueva `fuera_de_alcance` (quejas, reclamos, contratos o depósitos ya
  existentes): lo atiende una persona. Es distinta del fuera de dominio del guardrail de entrada
  (`A-8`, DT-15), que declina con una respuesta acotada y solo ofrece un asesor.
- **Frustración (proxy):** 3 turnos seguidos con intención `otro` (`FRUSTRATION_STREAK`). Se guarda en
  `fsm_context.unresolved_streak` (migración `0002`), se reinicia al resolverse un turno y tras el
  traspaso. La respuesta de ese turno avisa al lead ("te comunico con un asesor").
- **Cajón de sastre:** un `otro` suelto **no** dispara el traspaso: un "ok" o un "gracias" caen ahí y
  darle efecto real sería un traspaso por ruido. Solo cuenta repetido. Los turnos que fuerza el guardrail
  de entrada (*jailbreak*, fuera de dominio) **no suman ni reinician** la racha: nadie puede provocar un
  traspaso con mensajes adversariales, ni evitarlo intercalándolos.
- Los estados terminales no cuentan racha.

**Alternativas.** Que `otro` dispare el traspaso directamente — descartada por lo anterior. Guardar la
racha en `audit_log` o en la señal del score — descartada: mezcla responsabilidades. Contar mensajes
repetidos en vez de `otro` — descartada: necesita el historial completo (`A-2`).

**Consecuencias.** Con el clasificador falso, tres "ok" o "gracias" seguidos dejan un traspaso: es el
riesgo de usar `otro` como proxy y `A-2` debe revisarlo con el clasificador real y su taxonomía. La
respuesta de aviso se decide con la racha leída antes de la transacción; si dos mensajes de la misma
conversación llegan a la vez, el texto puede no coincidir con el estado (el estado siempre es el correcto).


## DT-17 — Métricas del funnel: una fila por transición real, escrita al guardar el estado

**Estado:** aceptada

**Contexto.** `OBS-2` pide `funnel_events` *append-only* con una fila por transición real de
`fsm_state`, no por turno. La tabla y el *trigger* que impide UPDATE/DELETE ya existían (`C-3`, DT-11).

**Decisión.**
- El evento se escribe en `StateStore.save_state`, en la misma transacción que el cambio de estado, con
  un `UPDATE ... WHERE fsm_state <> :nuevo RETURNING lead_id`: si el estado no cambió no hay fila, y
  toda transición (mensaje, *scheduler*) queda registrada sin que quien la provoque tenga que acordarse.
- `event_type` es `state.<ESTADO>` (`state.QUALIFY`): el estado al que **entra** la conversación. El
  prefijo deja sitio a otros tipos de evento sin que choquen con los nombres de estado.
- No hay cambios de esquema: la tabla sigue con `id, lead_id, event_type, created_at`.
- La entrada inicial a `GREETING` no es una transición y no se registra: el total de leads que empezaron
  sale de `leads`/`conversations`.

**Consulta para `B-9`** (leads distintos que alcanzaron cada etapa, en orden de primera aparición):

    SELECT event_type, count(DISTINCT lead_id) AS leads
    FROM funnel_events GROUP BY event_type ORDER BY min(id);

**Alternativas.** Una fila por turno — descartada: es lo que la historia excluye. Registrar el evento
desde `advance` — descartada: el *scheduler* (`NO_RESPONSE`) cambia el estado sin pasar por ahí. Añadir
`conversation_id`, `from_state` a la tabla — descartada por ahora: `B-9` no los necesita (la etapa
anterior se deduce del orden) y exigiría otra migración.

**Consecuencias.** Un lead que vuelve de `NURTURE` a `DISCOVERY` genera otra fila `state.DISCOVERY`: la
consulta de arriba lo cuenta una vez, pero quien quiera medir reentradas puede contar filas. Si `B-9`
necesita saber de qué estado se venía, hay que añadir `from_state` con una migración.



## DT-18 — Trazas por turno: OpenTelemetry hacia Phoenix, sin datos de los leads

**Estado:** aceptada

**Contexto.** `OBS-1` pide una traza por turno (estado de la FSM, tools, guardrails, latencia, costo),
con auto-instrumentación del SDK del proveedor de LLM (DT-6) y *spans* manuales. Phoenix ya está en el
compose (DT-9). Al probar la auto-instrumentación de Bedrock se vio que **graba por defecto el texto
completo de los mensajes** (`input.value`, contenido de cada mensaje), es decir, datos de los leads.

**Decisión.**
- `services/core/app/tracing.py` configura OpenTelemetry y exporta por OTLP/HTTP. Sin
  `OTEL_EXPORTER_OTLP_ENDPOINT` las trazas quedan apagadas y los *spans* son no-op. En el compose,
  `core` apunta a `http://phoenix:6006` y **no** depende de Phoenix: si cae, se pierden trazas pero el
  bot sigue (el exportador corre en segundo plano y sus fallos no llegan al turno).
- *Spans* manuales: `turn` (raíz, con estado de la FSM antes y después, *trigger*, motivo del traspaso,
  score, intención, tools usadas, resultado —`normal`/`jailbreak`/`out_of_domain`/`opt_out`/`withdrawn`—
  y latencia), `guardrail.input` (qué detectó), `fsm.advance` (la transacción de estado) y
  `catalog.<tool>` por cada llamada al catálogo.
- Auto-instrumentación: `openinference-instrumentation-bedrock` (modelo, tokens y tiempo de cada llamada)
  y `openinference-instrumentation-mcp`. `boto3` se añade a `core` porque el instrumentador lo necesita
  para activarse; `A-2` lo usará para llamar a Bedrock.
- **Privacidad:** las trazas nunca llevan el texto de los mensajes ni datos de contacto. Los *spans*
  manuales solo guardan identificadores, banderas y contadores; de las llamadas al catálogo solo los
  **nombres** de los argumentos, no sus valores. Al instrumentador de Bedrock se le configura ocultar
  el contenido y conservar tokens, modelo y tiempos. `TRACE_CONTENT=1` lo desoculta solo para depurar
  en local.
- Revisada la primera traza real (`tests/harness/test_traces_e2e.py`): un turno de búsqueda exporta
  exactamente `turn`, `guardrail.input`, `fsm.advance` y `catalog.search_properties`; el instrumentador
  de MCP solo propaga contexto y no añade *spans*. El test fija esa lista para notar si cambia.

**Alternativas.** Dejar el contenido visible en las trazas — descartada: mezcla datos de leads con la
herramienta de depuración. Exigir Phoenix para arrancar (`depends_on`) — descartada: la observabilidad no
debe poder tumbar el servicio.

**Consecuencias.** El costo por llamada al LLM sale de los tokens que registra el instrumentador
(`llm.token_count.*`) y lo calcula Phoenix a partir del modelo; falta verificarlo con una llamada real a
Bedrock (`A-2`). El fake LLM no genera *spans* de LLM. El catálogo no está instrumentado del lado del
servidor: su parte se ve como la duración del *span* `catalog.<tool>` en `core`. La imagen de `core`
crece unos 70 MB por `boto3`; encaja con `#65` (optimizar Dockerfiles).


## DT-19 — Agente con LLM real: historial en Postgres, tool calling y límite de tasa

**Estado:** aceptada — **con el LLM real sin verificar contra Bedrock** (ver Consecuencias)

**Contexto.** `A-2` pide que el orquestador arme el prompt con el historial leído de Postgres, use
*tool calling* real contra el catálogo y contemple desde el inicio el límite de tasa del proveedor
(DT-6: 10 solicitudes/minuto por modelo). Ya existían el cliente MCP (`A-3`), el guardrail de entrada
(`A-8`), el traspaso a humano (`A-13`) y las trazas (`OBS-1`).

**Decisión.**
- **Interfaz `Agent`** (`app/agent.py`) con dos implementaciones: `FakeAgent` (lo de siempre, sin
  credenciales) y `BedrockAgent`. Se elige con `LLM_PROVIDER` (`fake` por defecto). Una configuración
  inválida falla al arrancar (`app/llm_config.py`), no en el primer turno.
- **Dos llamadas por turno, a dos modelos** (DT-6): el modelo liviano clasifica la intención y extrae
  la señal cruda (`budget_hint`, `decision_maker`, `urgency`); el modelo conversacional responde con las
  tools del catálogo. Ambos a través de la API Converse (`app/bedrock_llm.py`). La respuesta del
  clasificador se parsea aunque venga en un bloque de código; una intención fuera de la taxonomía es `otro`.
- **Memoria en Postgres:** `core` guarda cada mensaje en `messages` **en la misma transacción** que el
  estado, la FSM y el score, y lee de ahí los últimos 20 mensajes en cada turno. No hay historial en memoria.
- **Qué ve el LLM y qué se guarda:** solo el texto filtrado por el guardrail de entrada, sin PII ni
  menciones de categorías protegidas (`InputVerdict.signal_text`, que conserva mayúsculas y acentos). Así
  esas menciones no llegan al modelo, no pueden orientar las recomendaciones (*steering*) y no quedan en
  la base. Un mensaje del que no queda nada utilizable se guarda como `[mensaje omitido]`; un intento de
  *jailbreak* también, para que no vuelva al LLM como historial. El clasificador solo recibe el mensaje
  actual y la última respuesta del asistente, nunca mensajes anteriores del lead.
- **`lead_qualified` lo fija el código:** se quita del esquema que ve el LLM y se inyecta en cada llamada
  a una tool con el valor de `fsm_context`. El modelo no puede pedir la dirección exacta (`A-6`).
- **Textos fijos** para las intenciones de traspaso a una persona (`solicitud_humano`,
  `fuera_de_alcance`), sin llamar al modelo conversacional. Jailbreak, fuera de dominio, mensajes vacíos
  o sin contenido utilizable tampoco gastan una llamada.
- **Si el LLM falla** (límite, red, credenciales, formato) el turno responde con un texto seguro, con
  intención `otro`: cuenta como turno sin resolver y tres seguidos llevan a `HUMAN_HANDOFF` (DT-16).
  Una caída del catálogo, en cambio, no se disfraza de fallo del LLM: sigue siendo un 503.
- **Límite de tasa** propio, sin Redis (`app/rate_limit.py`): ventana deslizante en memoria por modelo,
  10 por minuto (`LLM_RATE_LIMIT_PER_MINUTE`), con cola. Un turno espera cupo hasta 15 s
  (`LLM_MAX_QUEUE_WAIT_SECONDS`) y, si no lo hay, responde con el texto seguro.
- **Cajón de sastre (`otro`):** revisado en `A-13` (DT-16): un `otro` suelto no dispara ningún efecto, solo
  una racha. El agente real puede devolver `otro` cuando el modelo falla; eso cuenta como turno sin resolver.

**Alternativas.** Una sola llamada que clasifique y responda — descartada (DT-6): mezcla la extracción
silenciosa con la conversación visible. Dejar que el modelo pase `lead_qualified` — descartada: la
reserva de dirección no puede depender de un prompt. Guardar el texto original y filtrar al leer —
descartada: el dato sensible quedaría en la base.

**Consecuencias.**
- **No se ha probado una llamada real a Bedrock.** Las peticiones se validan contra el modelo de servicio de
  `botocore` (un `Stubber` rechaza parámetros mal formados) y todo lo demás con un cliente simulado. Los IDs
  de modelo de `.env.example` van vacíos a propósito: DT-6 advierte que algunos exigen un *inference
  profile*, y hay que probar una llamada de un token antes de darlos por buenos.
- **Sin guardrails de salida no debe usarse en producción.** `A-5` (factualidad), `A-6` (dirección),
  `A-7` (promesas) y `A-11` (fair housing) siguen pendientes: con `LLM_PROVIDER=bedrock` nada revisa lo que
  el modelo escribe salvo lo que dice el *prompt*. El *prompt* de sistema es una primera versión.
- El límite es por proceso: con varios *workers* sería N veces mayor. El compose corre uno.
- El historial guardado no incluye las menciones protegidas ni la PII de los leads: es deliberado y no se
  puede reconstruir el mensaje original desde la base.


## DT-20 — Canal de Telegram: bot embebido en `core`, identidad por `ON CONFLICT`, opt-out antes del LLM

**Estado:** aceptada

**Contexto.** `B-1..B-4` construyen el primer canal real (Telegram, `docs/ESTRUCTURA-DEL-PROYECTO.md`
línea 38: el canal vive dentro de `core`, no es un microservicio nuevo). Tres decisiones de diseño no
estaban escritas en ningún lado y afectan cómo cualquier canal futuro se integraría.

**Decisión.**
- **Ciclo de vida del bot:** `python-telegram-bot` (`Application`) arranca dentro del `lifespan` de
  FastAPI con `updater.start_polling()`, nunca `run_polling()` (que tomaría el control del *event loop*
  del servidor). Sin `TELEGRAM_BOT_TOKEN` el bot no arranca y `core` solo atiende `POST /events`: así
  CI, tests y el arnés E2E siguen funcionando sin credenciales de Telegram (DT-4).
- **Identidad del lead (`B-4`):** `telegram_user_id` resuelve al `Lead` real con
  `INSERT ... ON CONFLICT DO NOTHING` sobre el índice único, no derivando un id determinista con `uuid5`
  (que no evita una condición de carrera real). La unicidad la garantiza la base, no un supuesto del código.
- **Reuso de conversación:** un lead reutiliza su conversación mientras su `fsm_state` no sea terminal
  (`HANDOFF`/`HUMAN_HANDOFF`/`LOST`); al llegar a un estado terminal, el siguiente mensaje abre una
  conversación nueva. La alternativa de una única conversación permanente por lead (determinista,
  `uuid5(lead_id)`) se descartó: dejaría al lead sin forma de volver a hablar tras un estado terminal.
- **Opt-out (`B-2`):** la detección de baja (`/stop`, "date de baja", etc.) es determinista y corre en
  el canal, **antes** de construir el evento canónico — nunca pasa por `process_turn` ni por el LLM. La
  baja marca `Lead.consent_status=withdrawn` y `status=closed_lost` en una sola actualización y deja
  el mismo rastro de auditoría (`input.opt_out`) que la baja que detecta el guardrail del core. El
  criterio es la unión del canal y de `app/input_guard.py` (A-8): pedir la baja de cualquiera de las dos
  maneras la registra. La confirmación no promete borrar datos: solo registra la baja.
- **Un solo manejador de texto, comandos incluidos:** el bot atiende todo mensaje de texto, también
  `/stop`, que es la forma natural de pedir la baja; `/start <payload>` se traduce a texto vacío más el
  *deep-link*, y `/comando@bot` (grupos) se normaliza. Las actualizaciones se procesan en concurrencia
  para que un turno lento (p. ej. esperando cupo del LLM, DT-19) no bloquee a los demás leads, y si un
  turno falla el lead recibe una respuesta genérica en vez de silencio.
- **El agente lo elige `main`:** el bot recibe el mismo agente que `POST /events` (`LLM_PROVIDER`), así
  Telegram no queda hablando con el fake cuando se configura Bedrock (DT-19). Los logs de `httpx` se
  mantienen en WARNING porque la URL de la API de Telegram lleva el token.

**Alternativas.** Un microservicio de canal aparte, hablándole a `core` por HTTP — descartada: contradice
la estructura ya definida y agrega una llamada de red por turno sin necesidad, cuando el proceso ya
comparte engine y catálogo. Detectar el opt-out con el LLM (clasificar la intención y luego actuar) —
descartada: una baja de datos personales no puede depender de que un modelo la interprete bien.

**Consecuencias.** Cualquier canal nuevo (WhatsApp, web chat) sigue el mismo patrón: un módulo en
`app/channels/`, aislado de tipos externos, resolviendo identidad antes de construir el evento canónico.
Verificado en vivo contra un bot real de @BotFather: aviso de privacidad en el primer turno, deep-link
`?start=prop_<id>` resuelto, opt-out con `STOP`, y *long polling* confirmado por un `getUpdates` externo
devolviendo `409 Conflict`.

## DT-21 — Disponibilidad revalidada en código, con fallo cerrado

**Contexto.** El listado de `search_properties` y la ficha de `get_property` traen un `status`, pero es el
del momento de la consulta: una propiedad puede haberse apartado entre el listado y el mensaje al lead.
Dejarlo al *prompt* ("no ofrezcas propiedades apartadas") no es una garantía: el modelo puede ignorarlo.

**Decisión.**
- `AvailabilityGuard` (`services/core/app/availability.py`) envuelve al catálogo y `process_turn` se lo
  entrega a cualquier agente (el falso o el LLM real), así que no hay un camino que se lo salte. Tras
  `search_properties` y `get_property` llama a `get_availability` y sustituye el `status` por el vigente,
  con `availability_checked_at`.
- **Búsqueda:** en `properties` solo quedan las `disponible`. Las demás pasan a `unavailable` con su
  estado (`apartado`, `cerrado`, `sin_verificar`): así, si el lead pregunta por una concreta, se le dice
  la verdad en vez de negar que exista. El prompt del chat lo indica.
- **Fallo cerrado:** si `get_availability` falla o el catálogo no publica la tool, la propiedad queda
  `sin_verificar` y no se ofrece. Nunca se deja el `disponible` viejo.
- Solo se revalida lo que el listado marca `disponible` (lo demás no se ofrece de todos modos), en paralelo:
  como mucho una consulta por propiedad ofrecida, y ninguna si no hay coincidencias.
- Los resultados revalidados son los que se guardan en `last_tool_results`, la base de la factualidad
  (`A-5`), y de ahí sale `matching_properties` del scoring (`A-9`): cuenta solo propiedades realmente
  disponibles.

**Alternativas.** Reglas en el *prompt* — descartada: no es un guardrail. Filtrar en el propio catálogo —
descartada: el catálogo es un contrato externo (`C-1`) y `core` no debe fiarse de su listado para algo que
protege a la agencia. Revalidar también las no disponibles — descartada: una llamada más por propiedad sin
cambiar lo que se ofrece.

**Consecuencias.** Cada turno con búsqueda añade hasta N consultas de disponibilidad y sus *spans*
(`catalog.get_availability`), visibles en la traza. Un catálogo real con muchos resultados por búsqueda
puede pedir limitar el número de propiedades que se revalidan; `A-5` comparará contra estos mismos datos.


## DT-22 — Orquestación sin framework agéntico (LangGraph descartado)

**Estado:** aceptada

**Contexto.** Se evaluó adoptar LangGraph, aunque fuera solo para el bucle agente + tools. Hoy la
orquestación es código propio: la FSM es una tabla `(estado, trigger) -> estado` (`app/fsm.py`, DT-12),
el estado vive en Postgres (DT-1, DT-11), los guardrails son deterministas y corren antes de la FSM y del
LLM (DT-15, DT-21) y el bucle de tool calling son ~25 líneas en `BedrockLLM.chat` (DT-19). La pregunta
era si un framework simplificaría algo de eso o si solo añadiría una capa.

**Decisión.** No se adopta ningún framework agéntico. Se mantiene el enfoque actual: FSM en tabla, estado
transaccional en Postgres, bucle de tools propio detrás del `Protocol` `Agent` (`app/agent.py`). La
orquestación no depende de ningún proveedor de nube; la única dependencia de proveedor es el LLM
(`BedrockLLM`, DT-6), aislada detrás de ese mismo `Protocol`.

**Análisis por pieza.**

| Pieza | Con LangGraph | Veredicto |
|---|---|---|
| `fsm.py` (89 líneas, función pura) | Un `StateGraph` equivalente es más largo y deja de ser testeable sin el framework | Empeora |
| Estado en Postgres con bloqueo por conversación | El *checkpointer* de LangGraph sería una segunda fuente de verdad; ignorarlo deja el grafo como decoración | Choca con DT-1 |
| `advance()`: bloqueo, transición, score, auditoría y mensajes en una transacción | Un grafo por nodos fragmenta esa atomicidad | Riesgo |
| Guardrails de entrada (opt-out, jailbreak, fuera de dominio) | Serían aristas condicionales: mismas reglas, más maquinaria | Neutro |
| Bucle de tools de `BedrockLLM.chat` | Es lo único que reemplazaría; se pierde el control fino de `CODE_CONTROLLED_ARGUMENTS` (`lead_qualified`, A-6), del limitador por modelo y del fallback ante `LLMError` | Neutro a negativo |
| Trazas a Phoenix (DT-18) | Habría que sumar otro instrumentador y revisar spans duplicados | Neutro a negativo |

**Ventajas de omitirlo.**
- Se conserva una sola fuente de verdad del estado y la atomicidad del turno.
- Las garantías que protegen a la agencia (argumentos fijados por código, fallo cerrado de
  disponibilidad, guardrails antes del LLM) quedan a la vista, no dentro de nodos de un grafo.
- Menos dependencias y una imagen de `core` más pequeña; sin acople a la API de un framework que evoluciona.
- Sin migración ni riesgo de regresión en historias cerradas (`A-2`, `A-3`, `A-4`, `OBS-1`) ni en las
  abiertas que tocan el bucle (`A-14`, `A-5`, `A-6`, `A-7`).
- Cambiar de proveedor de LLM sigue siendo escribir otro `Agent`, sin tocar la FSM.

**Desventajas aceptadas.**
- Se mantiene código propio que un framework daría hecho: el bucle de tools, los reintentos (`A-14`) y la
  gestión del historial.
- `_run_turn` (`app/turn.py`) concentra muchas ramas y es la función más difícil de leer. Se atiende
  partiéndola en funciones, no con un framework.
- No hay soporte incorporado para *streaming*, interrupciones de *human-in-the-loop* ni ejecución
  paralela de tools. Nada de eso está en el alcance actual.
- Un equipo que ya conozca un framework tendría menos que aprender.
- Si la consigna del curso exige un framework agéntico con nombre, esta decisión no la cumpliría a la
  letra (ver condición de revisión).

**Alternativas.**
- *LangGraph en el bucle agente + tools, dejando FSM y guardrails como están* — descartada por ahora: su
  impacto es bajo (cambia `BedrockLLM.chat` y `BedrockAgent`, y roza `A-14`, `A-5`, `A-6` y `A-7`) pero
  no aporta capacidad que hoy se necesite. Es el plan de contingencia si la consigna lo exige.
- *LangGraph para toda la orquestación (FSM incluida)* — descartada: choca con DT-1 y DT-12.
- *Pydantic AI, CrewAI, DeepAgents, Strands Agents* — descartadas. Los tres últimos están pensados para
  varios agentes colaborando o atan a un proveedor; este flujo es un agente con una FSM.

**Consecuencias.**
- La costura para un cambio futuro es el `Protocol` `Agent`: un framework, si se adopta, entra dentro de
  `BedrockAgent` sin tocar `turn.py`, la FSM ni `advance()`.
- `A-14` (reintentos con backoff) se implementa en `BedrockLLM._converse`, sin depender de ningún framework.

**Condición de revisión.** Reabrir esta decisión si (a) la consigna del curso exige un framework agéntico
concreto, o (b) el flujo pasa a tener varios agentes, *streaming* o interrupciones humanas. En el caso (a),
el alcance mínimo es un `StateGraph` de dos nodos (llamar al modelo ⇄ ejecutar tools) que siga usando
`BedrockLLM._converse`, con una entrada nueva que reemplace a esta.

## DT-23 — Factualidad de salida: comparación literal, sin comprensión semántica

**Contexto.** El LLM real (`A-2`, DT-19) puede inventar un precio, un tamaño o un estado que suenen
plausibles pero que no vengan de ninguna tool. `A-5` exige que eso nunca llegue al lead: todo número
o estado de la respuesta candidata tiene que existir en `last_tool_results` de ese mismo turno.

**Decisión.**
- `app/factuality.py` extrae los números y los estados de propiedad (`disponible`/`apartado`/`cerrado`/
  `sin_verificar`, en cualquier forma gramatical) tanto de la respuesta candidata como de
  `last_tool_results` del turno, y compara por **igualdad literal de texto** — sin NLP, sin mapear
  qué número pertenece a qué campo. Es deliberadamente simple: la garantía que necesita la agencia es
  "esto no se lo inventó", no "esto es coherente".
- Un número también se admite si viene del propio mensaje del lead (repetir su presupuesto no es
  inventar). El tamaño de una lista cuenta como número legítimo solo para `properties` y
  `promotions` — no para metadata como `unavailable` (A-4) o `amenities`, que casi siempre tienen 1
  o 2 elementos y volverían inútil la protección contra una "urgencia falsa" (`solo queda 1`, A-7).
- `BedrockAgent.run` (`app/agent.py`) aplica el guardrail después de `BedrockLLM.chat`: si hay
  violaciones, `BedrockLLM.regenerate` repite la conversación con la respuesta candidata y una
  instrucción explícita de qué no se pudo verificar — sigue teniendo las tools disponibles, así que
  puede corregirse consultando de nuevo. Si la respuesta regenerada también falla, se degrada a un
  texto fijo que ofrece un asesor, con `intent="otro"` (no fuerza `HUMAN_HANDOFF`: si el guardrail
  degrada varias veces seguidas, la racha de turnos sin resolver que ya existía, `app/handoff.py`,
  lo hace transicionar sola — sin una segunda máquina de estados para "fallos del guardrail").
- Cada regeneración y cada degradación quedan en `AuditLog` (`output.factuality_regenerated`,
  `output.factuality_degraded`) con el conteo de violaciones, nunca el número o estado inventado, y
  en la traza del turno (`turn.output_guard`).
- El gold set de la historia (≥30 casos, `tests/test_factuality_gold_set.py`) no llama a ningún LLM:
  prueba `factuality.check()` directamente contra pares (respuesta candidata, `tool_results`) fijos,
  así corre en CI sin credenciales y en milisegundos. Cubre tanto respuestas correctas que no deben
  bloquearse como respuestas inventadas que sí, y dos límites conocidos del enfoque literal.

**Límite conocido, aceptado.** La comparación no sabe DE QUÉ propiedad habla el texto: si un número es
real en cualquier resultado de este turno (p. ej. el título de una propiedad no ofrecida, que
menciona sus recámaras en prosa), se admite aunque la respuesta lo esté atribuyendo a otra. Cerrar
esto exigiría atar cada claim a la propiedad que menciona el texto (NLP o al menos un LLM juez), que
es justamente la complejidad que esta decisión evita. Documentado con un caso propio en el gold set
(`numero_real_de_otra_propiedad_del_turno`) para que no se pierda de vista.

**Alternativas.** Un segundo LLM como juez de factualidad — descartada: otra llamada por turno, otro
punto de fallo, y su propio veredicto también podría alucinar. Exigir que el LLM cite el campo exacto
de donde sacó cada dato (grounding estructurado) — mejor a largo plazo, pero exige rediseñar
`CHAT_SYSTEM` y el formato de respuesta; queda para si el límite conocido resulta ser un problema real.

**Consecuencias.** Cada respuesta con datos de propiedades cuesta hasta una llamada más al modelo
(la regeneración) cuando falla la primera vez; con cuota baja de Bedrock, eso se suma al problema que
ya cubre `A-14`. `A-6` (dirección reservada) y `A-7` (promesas no autorizadas) reutilizan `check()`
para sus propias listas de valores prohibidos/reservados.

## DT-24 — Lead calificado: la banda del score, marcado en `leads.status`, avisado una sola vez

**Estado:** aceptada

**Contexto.** `B-7` pide notificar al asesor "en el turno exacto en que el lead pasa a calificado". Esa
señal no existía: `FsmContext.lead_qualified` nunca se escribía, `Lead.status` tampoco, y DT-14 decidió
que el score BANT-E no condiciona las transiciones de la FSM. Hacía falta decidir qué cuenta como
"calificado", cómo se garantiza que el aviso llegue una sola vez, y a quién.

**Decisión.**
- **Calificado = la banda del score cruza a WARM o HOT** (total ≥ 41). Es el significado de negocio de
  BANT-E: vale la pena que un asesor lo llame. Es independiente del estado de la FSM y no contradice
  DT-14 — solo *lee* la banda que ya se calcula, no mueve estados. La detección vive en
  `services/core/app/qualification.py`, comparando la banda anterior (`state.score`) con la nueva.
- **La guarda de idempotencia es la propia fila:** `UPDATE leads SET status='mql' WHERE id=:id AND
  status='nuevo' RETURNING id` en la misma transacción del turno (`StateStore.qualify`). El segundo turno
  que cruce la banda no devuelve fila y no notifica. De paso reviven `Lead.status` (nuevo → mql) y
  `FsmContext.lead_qualified`, que el agente ya usa para decidir si puede revelar la dirección exacta.
- **El aviso sale del canal, después del commit:** `process_turn` recibe un `on_qualified` opcional y lo
  invoca solo cuando `Transition.qualified` es verdadero. Lo pasa el canal de Telegram; el `POST /events`
  del arnés no, así los E2E nunca escriben a nadie. Un fallo del aviso se registra y no toca el turno.
- **A quién:** el asesor de la misma `Lead.zone` que ya abrió el bot; si no hay, cualquiera registrado
  (cola general, patrón de v1). Queda escrito en `leads.advisor_id` (migración `0003`).
- **Registro del asesor por deep-link:** `t.me/<bot>?start=adv_<access_token>` liga el chat al asesor
  dueño del token (`advisors.telegram_chat_id`, migración `0003`). Se usa el token y no el `advisor_id`
  a propósito: `adv_001` es adivinable. Un chat pertenece a un solo asesor.

**Alternativas.** Usar la transición de FSM a `RECOMMEND` como "calificado" — descartada: hoy la dispara
la intención del LLM (`consulta_precio`), no la calidad del lead. Idempotencia por `AuditLog` o por la
banda anterior — descartadas: una fila de estado es más simple, atómica y además le da sentido a un
campo que ya existía. Notificar desde dentro de `advance` — descartada: mandaría el mensaje con la
transacción todavía abierta.

**Consecuencias.** `Lead.status` pasa a tener escritor: `nuevo` → `mql` al calificar; `B-6` lo mueve a
`sql` cuando el asesor lo toma. La propiedad de interés va en la tarjeta **solo si el turno que califica
la miró**: `FsmContext.last_tool_results` se sobrescribe cada turno (A-9) y la propiedad concreta no se
persiste en otro lado (la zona sí, #81). Cerrar eso pide una columna nueva, y no la justifica todavía.
La tarjeta no lleva datos de contacto: esos los ve el asesor en el panel, con su token.

## DT-25 — Dirección reservada: se detecta la FORMA de una dirección exacta, no un valor real

**Estado:** aceptada

**Contexto.** `A-6` pide bloquear una dirección exacta mencionada en prosa cuando el lead no está
calificado, aunque no venga de un campo estructurado. `DT-23` anticipaba que `A-6` reutilizaría
`factuality.check()` con "sus propias listas de valores prohibidos". Al implementarla resultó que eso
no es posible: `get_property`/`search_properties` (`services/catalog/app/service.py`) nunca devuelven
`address_exact` salvo con `lead_qualified=true` — mientras el lead no está calificado, `core` no tiene
ningún valor real de dirección contra el que comparar. Una comparación literal como la de A-5 no tiene
nada que comparar; hacía falta otro enfoque.

**Decisión.**
- `app/address_guard.py` detecta la **forma** de una dirección exacta — tipo de vía (calle, avenida,
  circuito, blvd...) seguido de un nombre y un número — no si coincide con un dato real. Es lo mismo
  que distingue `address_exact` de `address_approx` en el propio catálogo: la referencia aproximada
  ("cerca de Av. Vallarta") nunca lleva número: el número es la señal.
- Como no depende de si el número "está permitido" por las tools de este turno, cubre un caso que A-5
  no puede: un número de calle que coincide por casualidad con otro dato real del turno (precio, m²)
  y que A-5 dejaría pasar por estar "respaldado" — la dirección sigue siendo una dirección.
- Se integra en el mismo *pipeline* de `BedrockAgent` que A-5 (`app/agent.py::_output_violations`):
  ambos guardrails devuelven `factuality.Violation` y comparten regenerar (1er fallo) y degradar (2do
  fallo, `FACTUALITY_DEGRADE_REPLY`). No hay una acción de auditoría separada para A-6: la mismas
  `output.factuality_regenerated`/`output.factuality_degraded` ya describen lo que pasó (el turno se
  rehizo o se degradó), sin importar cuál de los dos guardrails lo disparó.
- Como A-5, se exime lo que el propio lead ya escribió (su propia dirección, para preguntar cerca de
  dónde busca no es una fuga de la propiedad).
- Como A-5, solo aplica a `BedrockAgent`: `FakeAgent` nunca ve `address_exact` porque el catálogo la
  redacta antes de que llegue a cualquier agente.

**Límite conocido, aceptado.** Un patrón de texto, no NLP (mismo espíritu que DT-23): una distancia
("a 5 minutos de Av. Vallarta") podría confundirse con una dirección si el número cae pegado al
nombre de la calle — se acota con una salvedad para palabras de unidad (minutos, cuadras, km) justo
después del número, cubierta en el gold set (`test_address_guard_gold_set.py`), pero no es una prueba
exhaustiva de lenguaje natural.

**Alternativas.** Reutilizar `factuality.check()` literalmente, con la dirección real como "valor
prohibido" — descartada: exigiría que `core` consultara el catálogo con `lead_qualified=true` solo
para el guardrail (una llamada extra, y una ruta que ve el dato que se supone debe ocultar). Un
guardrail que solo mire si el texto contiene el *approx* — descartada: no detecta un número inventado
ni uno real que se cuele en prosa.

**Consecuencias.** Una dirección con número casi siempre también dispara A-5 (el número no viene de
ninguna tool mientras el lead no está calificado): las dos redes coinciden a propósito, no es
redundancia — ver `test_an_address_in_prose_gets_one_chance_to_regenerate_and_the_fix_is_used`.

## DT-26 — Promesas no autorizadas: frases siempre prohibidas + amenidad literal, 6/8 con código

**Estado:** aceptada

**Contexto.** `A-7` pide bloquear 8 *claims* de `docs/DESIGN/guardrails.md` §2.2 que comprometen a la
agencia con algo que el sistema no garantiza. La historia no exige cubrir los 8 desde el día 1 (v1
cubrió 6/8 sin dejarlo documentado — la lección explícita de v1 es que la tabla de `guardrails.md`
refleje el estado real de cada uno, cubierto o no, no solo la lista de qué debería existir).

**Decisión.**
- **Frases siempre prohibidas** (*claims* 1 crédito, 2 mascotas, 3 negociación, 4 apartar, 7 garantía
  de cierre): ninguna tool ni dato del sistema las respalda hoy — no existe una tool de negociar
  precio ni de "apartar" una propiedad, y el catálogo no tiene ningún campo de política de mascotas
  (`services/catalog/app/data.py`). Se bloquean por frase, sin mirar `last_tool_results`, mismo estilo
  que el guardrail de entrada (`app/input_guard.py`).
- **Amenidad no respaldada** (*claim* 5): comparación literal contra `amenities` del turno, acotada al
  vocabulario real del catálogo (mismo principio que A-5, DT-23) — no NLP.
- Ambos mecanismos comparten una salvedad: negarlo o dudarlo ("no aceptamos mascotas", "voy a
  confirmar si...") no es prometerlo — una palabra de negación/duda hasta 3 palabras antes de la frase
  la excluye (`promise_guard._unhedged`).
- Se integra en el mismo *pipeline* de `BedrockAgent` que A-5/A-6 (`app/agent.py::_output_violations`):
  los tres devuelven `factuality.Violation` y comparten regenerar/degradar y la misma auditoría.
- **Claim 8 (urgencia falsa) no necesita código nuevo**: ya lo cubre A-5 — un número sin respaldo en
  `last_tool_results` bloquea, con o sin la palabra "urgencia" (`test_factuality_gold_set.py::
  urgencia_falsa_con_numero`, escrito en A-5 antes de que A-7 existiera).
- **Claim 6 (promoción no vigente) queda parcial, documentado, no oculto**: con número (un descuento o
  porcentaje inventado) ya lo bloquea A-5 igual que cualquier otro dato inventado. Una promoción
  genérica sin número ("tenemos una promoción especial para ti") no se cubre: comparar texto libre
  contra el título/descripción real de una promoción es la misma clase de problema que un guardrail de
  patrones no puede resolver de forma confiable sin subir el riesgo de falsos positivos/negativos muy
  por encima del resto de esta historia.

**Alternativas.** Un juez con LLM para las 8 — descartada: mismo motivo que A-5 (DT-23), otro punto de
fallo que también puede alucinar. Exigir los 8 con código en esta historia — descartada: la propia
historia lo permite explícitamente a cambio de documentar la brecha, y forzar el 6 (promoción
genérica) a un patrón simple habría sido una protección débil disfrazada de completa.

**Consecuencias.** El estado real de cada uno de los 8 *claims* vive en `docs/DESIGN/guardrails.md`
§2.2 y §3, no solo en el docstring de `promise_guard.py` — se actualiza en este mismo PR, como pide la
retrospectiva de v1. Cerrar el *claim* 6 por completo es trabajo futuro sin historia asignada todavía.

## DT-27 — Logging JSON centralizado, incluidos los loggers propios de uvicorn; sin caso real de PII en logs

**Estado:** aceptada

**Contexto.** `OBS-7` pide logs en JSON en los 3 servicios (`core`, `panel`, `catalog`), incluyendo
los *handlers* propios del servidor ASGI elegido — no solo el *root logger*, porque `uvicorn`
configura sus propios loggers (`uvicorn`, `uvicorn.access`) antes de que la app arranque, y 2 de ellos
tienen `propagate=False` (`uvicorn.config.LOGGING_CONFIG`), así que reconfigurar solo `root` no los
alcanza. La historia también pide una decisión explícita sobre PII en logs, incluso si la conclusión
es que no hay caso real que enmascarar (v1 llegó a esa misma conclusión, pero sin documentarla).

**Decisión.**
- `db/logging_config.py::configure_json_logging()`, llamado a nivel de módulo en la primera línea
  ejecutable después de los imports de cada `main.py` (`services/{core,panel,catalog}/app/main.py`),
  antes de construir `FastAPI(...)`. Los 3 servicios arrancan vía `uvicorn app.main:app` por CLI sin
  `--log-config`; `Config.__init__` de uvicorn corre su propio `configure_logging()` (texto plano)
  antes de `Config.load()`, que es quien importa `app.main` — así que este segundo `dictConfig`
  corre después y gana, sin condición de carrera ni necesidad de tocar el `CMD` de ningún Dockerfile.
- Un único `JsonFormatter` (una línea de JSON por registro: `timestamp`, `level`, `logger`, `message`,
  `exc_info` si aplica) aplicado a `root` y, explícitamente, a `uvicorn`/`uvicorn.error`/
  `uvicorn.access` — nombrarlos aparte es lo que cierra el hueco de `propagate=False`.
- `db/` ya es el paquete compartido por los 3 servicios (`docs/ESTRUCTURA-DEL-PROYECTO.md` §2), así
  que no hace falta duplicar el módulo ni tocar `PYTHONPATH`/Docker.
- PII: sin caso real hoy — ningún `logger.*` en `core`/`panel`/`catalog` imprime texto del lead, solo
  mensajes fijos y metadata. Documentado explícitamente en `docs/DESIGN/guardrails.md` §3 (no en
  silencio, como pide la nota de v2 de la propia historia).

**Alternativas.** `structlog`/`python-json-logger` — descartadas: dependencia nueva en los 3
`requirements.txt` para algo que un `logging.Formatter` de ~25 líneas sin dependencias resuelve. Pasar
`--log-config` en el `CMD` del Dockerfile — descartada: exige un archivo de configuración separado por
servicio (JSON/YAML) para algo que un `dictConfig` en Python, versionado y testeable con `pytest`
junto al resto del código, resuelve con menos superficie.

**Consecuencias.** Cualquier log nuevo que quiera llevar datos del lead debe redactarlo antes de
loguear (mismo principio que `input_guard.py`/`InputVerdict.audit_kinds()`: tipos, nunca contenido).
Si en el futuro aparece un caso real de PII en logs, se actualiza `docs/DESIGN/guardrails.md` §3 y
esta entrada en el mismo PR que lo introduce, no después.

## DT-28 — Ficha de traspaso: en `HANDOFF` y `HUMAN_HANDOFF`, con la transcripción completa y una sola notificación por turno

**Estado:** aceptada

**Contexto.** `A-12` pide que el asesor reciba una ficha completa (score, propiedad de interés, visita,
transcripción) cuando el lead pasa a traspaso. El título habla de `HANDOFF`, pero el criterio de
aceptación dice `HUMAN_HANDOFF`: son dos estados distintos de la FSM (DT-12). `HANDOFF` es el traspaso
normal, tras agendar la visita; `HUMAN_HANDOFF`, el que provoca una petición explícita, una consulta fuera
de alcance o la frustración (DT-16). Además ya existe la tarjeta de calificación de `B-7` (DT-24): había
que evitar que un mismo turno mandara dos avisos y que un lead cambiara de asesor a mitad del camino.

**Decisión.**
- **Se dispara al llegar a cualquiera de los dos estados**, en el turno exacto de la transición. Ambos
  significan "una persona toma el lead" y el asesor necesita la misma ficha. `handoff.handoff_reason()`
  devuelve el motivo (`solicitud_humano`, `fuera_de_alcance`, `frustration`, `visit_scheduled`) solo en
  ese turno: los estados de traspaso son terminales, así que el mensaje siguiente no vuelve a disparar.
- **Mismo mecanismo que `B-7`:** `process_turn` recibe un `on_handoff` opcional, que invoca después del
  commit cuando `Transition.handoff_reason` no es nulo. Lo pasa el canal de Telegram; el `POST /events`
  del arnés no. Un fallo al enviar se registra y no toca el turno del lead.
- **Contenido:** motivo, banda y total del score con sus razones, zona, propiedad de interés, visita y
  enlace al panel, más la **transcripción completa** de la conversación (lead y agente, en orden,
  incluido el turno del traspaso). Sin datos de contacto, igual que la tarjeta de calificación.
- **La transcripción se parte, no se recorta:** Telegram limita cada mensaje a 4096 caracteres, así que
  el resumen va en el primer mensaje y la transcripción en los siguientes, cortada por líneas
  (`split_lines`); una línea más larga que el tope se corta a la fuerza. Ninguna parte se pierde.
- **Qué texto ve el asesor:** el mismo que ya guarda la base (`messages`): el mensaje del lead pasó por
  el guardrail de entrada (PII y categorías protegidas ya redactadas, DT-15) y la respuesta es la que se
  le envió. La ficha no introduce ningún dato nuevo.
- **A quién:** el asesor que ya tenía el lead (`Lead.advisor_id`) si sigue registrado en Telegram; si no,
  el de su zona o la cola general, que queda asignado. Un lead ya calificado no cambia de asesor en el
  traspaso.
- **Una sola notificación por turno:** si el mismo turno califica al lead y lo traspasa, se manda solo la
  ficha de traspaso (ya lleva el score). El lead queda marcado como calificado igual (`qualified_at`,
  DT-24).

**Alternativas.** Mandar la transcripción como archivo adjunto — descartada: `Sender` solo tiene
`send_message` y añadir documentos abre una superficie nueva por algo que el texto en varios mensajes
resuelve. Recortar la transcripción a los últimos N mensajes — descartada: el criterio de aceptación pide
la transcripción completa.

**Consecuencias.** La entrega es de mejor esfuerzo y una sola vez, como en `B-7`: si Telegram falla en
ese turno, la ficha no se reintenta (el asesor sigue viendo al lead y su transcripción en el panel,
`B-5`). Una cola con reintentos pertenece a una historia aparte, no a esta. Una conversación muy larga
genera varios mensajes seguidos al asesor; es el costo aceptado de no perder nada.

## DT-29 — La conversación nueva de un lead existente hereda la señal del score y la calificación

**Estado:** aceptada

**Contexto.** DT-20 abre una conversación nueva cuando la última quedó en estado terminal, con un
`FsmContext` vacío. Pero el score (`lead_scores`) y la calificación (`Lead.qualified_at`, DT-24) son **por
lead**, mientras que `score_signal` y `lead_qualified` vivían solo en el contexto de la conversación.
Visto en vivo (#84): un lead en WARM 60 que fue traspasado y escribió una vez más quedó en COLD 0 en
`lead_scores` y en la cola del panel, porque el primer turno recalculó el score desde una señal vacía y
`save_scoring` lo sobreescribió. Lo mismo pasaba con `lead_qualified`: `StateStore.qualify` solo lo
escribe al reclamar `qualified_at`, que ya no está libre, así que la conversación nueva nunca volvía a
saber que el lead estaba calificado y el agente dejaba de poder revelarle la dirección exacta (A-6).

**Decisión.**
- Al abrir una conversación nueva para un lead que ya tuvo otra, `resolve_conversation`
  (`channels/identity.py`) copia `score_signal` y `lead_qualified` del `FsmContext` de la **última**
  conversación. Lo que declaró el lead (presupuesto, quién decide, urgencia) sigue siendo cierto.
- Lo que es de la conversación arranca limpio: racha de frustración, resultados de tools del último turno,
  historial de mensajes.
- Es la opción 1 del issue #84. No se cambia el esquema ni el contrato `FsmState` (CT-3).

**Alternativas.** Que `save_scoring` nunca baje el `total` — descartada: esconde que la señal se perdió
y no arregla `lead_qualified`. Mover `score_signal` a nivel de lead — descartada: migración y cambio de
contrato para un problema que se resuelve al abrir la conversación.

**Consecuencias.** Un lead que vuelve tras un traspaso conserva su banda; si su interés cambió, el score
lo refleja turno a turno como siempre. Los eventos de un lead desconocido que entran por el arnés
(`StateStore._ensure_rows`) no pasan por aquí y siguen creando el contexto vacío; cada guion del arnés
usa una sola conversación fija (ids derivados del nombre del guion) y nunca abre una segunda.

## DT-30 — Reintentos de Bedrock: solo errores transitorios, espera exponencial con jitter y un tope de tiempo por turno

**Estado:** aceptada

**Contexto.** Con una cuota baja, Bedrock responde `ThrottlingException` a pocas llamadas seguidas (una por
el clasificador y una o más por el chat con herramientas) aunque el limitador local (DT-6, por minuto) no
esté lleno. Hasta ahora cualquier error del proveedor terminaba en el texto de respaldo, y el lead veía
"tuve un problema para procesar tu mensaje" por algo que esperar un segundo resolvía (`A-14`). DT-22 ya
dejó dicho que los reintentos son código propio, dentro de `BedrockLLM._converse`.

**Decisión.**
- **Solo se reintenta lo transitorio:** `ThrottlingException`, `ServiceUnavailableException`,
  `ModelTimeoutException`, `InternalServerException` y, entre los errores de conexión de botocore,
  `EndpointConnectionError`, `ConnectTimeoutError`, `ReadTimeoutError` y `ConnectionClosedError`. Es una
  lista explícita y no la jerarquía de botocore: `ReadTimeoutError` y `ConnectionClosedError` no heredan de
  `ConnectionError`, y un error de SSL o de proxy es de configuración. Todo lo demás (`AccessDenied`,
  `Validation`, modelo inexistente, credenciales) falla al primer intento.
- **Espera:** `base * 2^(intento-1)` con tope `max_delay`, mitad fija y mitad al azar (*jitter*): así
  turnos concurrentes que fallaron a la vez no reintentan en el mismo instante y siempre hay una espera
  mínima. Si Bedrock manda `Retry-After` (segundos), se espera eso en lugar de lo calculado; un valor que
  no es un número se ignora.
- **Tope de tiempo por turno, no por llamada:** la ventana (`LLM_RETRY_MAX_TOTAL_SECONDS`) se abre con el
  primer fallo transitorio del turno y la comparten el clasificador, el chat y la regeneración de A-5
  (`BedrockLLM.turn()`, una `ContextVar` que abre `BedrockAgent.run`). Una espera que no cabe en lo que
  queda no se hace: el turno cae a `LLM_FALLBACK_REPLY`. Vale también para el `Retry-After`: esperar menos
  de lo que pide el proveedor no sirve.
- **Cada intento pasa por el limitador de A-2:** un reintento no se salta el cupo por minuto. Y la espera
  de cupo de un reintento se recorta a lo que le queda al turno, para que el tope sea real.
- **Configuración:** `LLM_RETRY_MAX_ATTEMPTS` (4), `LLM_RETRY_BASE_DELAY_SECONDS` (0.5),
  `LLM_RETRY_MAX_DELAY_SECONDS` (8) y `LLM_RETRY_MAX_TOTAL_SECONDS` (20), validadas al arrancar
  (`ConfigError`). `MAX_ATTEMPTS=1` los desactiva. Un `BedrockLLM` construido sin `Retrier` no reintenta:
  los límites reales los pone `llm_config.build_agent`. Los reintentos propios de botocore siguen
  apagados (`max_attempts=1`): hay un solo dueño de la política.
- **Observabilidad:** cada reintento y el fallo definitivo salen como `warning` con el código del error,
  el número de intento y el motivo de rendirse; nunca el texto del lead ni el mensaje de la excepción. El
  fallo definitivo, además, queda como atributos y evento del *span* del turno (`llm.error_code`,
  `llm.attempts`, `llm.give_up_reason`).

**Alternativas.** Dejar que botocore reintente (`retries={"mode": "adaptive"}`) — descartada: no comparte
el tope de tiempo entre las llamadas de un turno, no pasa por el limitador y sus reintentos no quedan en
nuestro log ni en la traza. `tenacity` u otra librería — descartada: una dependencia nueva para un módulo pequeño
con reloj inyectable (~140 líneas con su documentación), que es lo que permite probarlo sin esperas reales.

**Consecuencias.** En el peor caso un turno tarda `max_total` (20 s) más la duración de una llamada antes
de caer al texto seguro; con el límite de 15 s de cola de A-2 eso sigue acotado. El tiempo se cuenta
desde el primer fallo transitorio, no desde el inicio del turno: las llamadas que van bien antes del primer
fallo no consumen el tope.

**Verificación en vivo (2026-09-23, Haiku 4.5 en `us-east-1`, cuota de ~10 solicitudes por minuto y modelo).**
Ráfaga de 8 turnos seguidos con el limitador local apagado (`LLM_RATE_LIMIT_PER_MINUTE=1000`), para que
respondiera la cuota real: Bedrock devolvió 36 `ThrottlingException`, todos reintentados, ninguno terminó
en fallo definitivo y los 8 turnos respondieron con el modelo, ninguno con el texto de respaldo. El turno
más lento tardó 19 s, cerca del tope de 20 s. Bedrock no mandó `Retry-After` en ninguno: se usó la espera
calculada (la ruta con cabecera solo está cubierta por los tests).

**Interacción con el limitador local (A-2).** Con la configuración por defecto (`LLM_RATE_LIMIT_PER_MINUTE=10`,
igual a la cuota de Bedrock) el limitador local actúa antes que Bedrock: en la misma ráfaga solo 2 de 8
turnos respondieron y el resto cayó al texto de respaldo por `RateLimited`, sin un solo throttling de
Bedrock y sin reintentos, porque un cupo local agotado no es un error transitorio y no se reintenta. Es el
comportamiento de A-2, sin cambios. Además, cada reintento consume un cupo local (lo pide la historia),
así que con el limitador activo los reintentos también lo agotan. Para que una ráfaga la absorba el
*backoff* y no el texto de respaldo, `LLM_RATE_LIMIT_PER_MINUTE` debe ser mayor que la cuota real de
Bedrock, y el precio son turnos de hasta ~20 s. Es una decisión de despliegue (protección de costo contra
latencia) que este PR no toma: deja el valor por defecto en 10.

## DT-31 — Plan B de observabilidad: Jaeger por OTLP, y sin trazas si tampoco hay Jaeger

**Estado:** aceptada — documentada, no implementada

**Contexto.** `OBS-8` pide una alternativa concreta por si Phoenix (DT-9) falla, sin implementarla a
menos que haga falta. Fallar puede ser que la imagen deje de arrancar, que una versión nueva rompa la
recepción de trazas o que consuma más recursos de los que la máquina de la demo tiene. Dos decisiones
anteriores acotan el problema. Las trazas son OpenTelemetry estándar por OTLP/HTTP, así que no están
atadas a Phoenix (DT-9, DT-18). Y `core` no depende de Phoenix para arrancar: si cae, se pierden las
trazas pero el bot sigue respondiendo (DT-18).

**Decisión.** Dos niveles, del más completo al más simple.

- **Nivel 1: Jaeger en lugar de Phoenix.** Jaeger v2 (`jaegertracing/jaeger:2.21.0`) es un solo
  contenedor, sin Redis ni otros servicios (DT-3), que recibe OTLP/HTTP en el puerto `4318`. El cambio
  es solo del compose:
  1. Agregar el servicio `jaeger` con esa imagen y la UI publicada en `16686`.
  2. En `core`, cambiar `OTEL_EXPORTER_OTLP_ENDPOINT` de `http://phoenix:6006` a `http://jaeger:4318`.
  3. Quitar o dejar parado `phoenix`.

  No cambia código: `tracing.py` usa el exportador OTLP estándar, que agrega `/v1/traces` a la URL.
- **Nivel 2: sin trazas.** Quitar `OTEL_EXPORTER_OTLP_ENDPOINT` del compose. Las trazas se apagan y
  los *spans* pasan a ser no-op (DT-18). Lo que sigue funcionando sin ninguna herramienta extra:
  - Los logs JSON de los tres servicios (DT-27), incluidos los avisos de reintento y fallo de Bedrock
    (DT-30).
  - `funnel_events` y el dashboard del funnel del panel (DT-17, `B-9`).
  - `audit_log`, con los guardrails que se dispararon (DT-15, DT-23).

**Verificado (2026-09-24).** Con un Jaeger 2.21.0 desechable en la red del compose y `core` cambiando
solo `OTEL_EXPORTER_OTLP_ENDPOINT`, un turno real por `POST /events` llegó a Jaeger con los mismos cuatro
*spans* que DT-18 fija para Phoenix: `turn`, `guardrail.input`, `fsm.advance` y
`catalog.search_properties`. La API de Jaeger (`/api/v3/traces`) los devolvió con sus atributos. La
prueba no dejó nada en el repo.

**Qué se pierde con Jaeger.** Las vistas propias de LLM de Phoenix: el costo por llamada calculado a
partir del modelo y la presentación de cada *span* según su tipo (`openinference.span.kind`). Los
atributos siguen en la traza (`llm.token_count.*`, modelo, tiempos), pero se leen como atributos
sueltos. Con el almacenamiento en memoria por defecto, las trazas se pierden al reiniciar el
contenedor. Para depurar un turno alcanza; para comparar entre días, no.

**Alternativas.** Langfuse autoalojado, descartada por lo mismo que en DT-9: exige Redis y varios
servicios. Grafana Tempo, descartada: necesita Grafana aparte para ver las trazas, dos contenedores
donde Jaeger usa uno. Exportar las trazas a archivo o a consola, descartada como plan B: no hay forma
cómoda de ver un turno completo, y para eso ya están los logs JSON del nivel 2.

**Consecuencias.** El plan B no toca `services/`, así que no hay nada que mantener mientras no se use.
Los tests no dependen de Phoenix: `test_tracing.py` exporta en memoria y `tests/harness/test_traces_e2e.py`
usa un receptor OTLP propio. Si se activa el nivel 1, hay que actualizar `tests/infra/test_compose.py`,
que hoy exige `http://phoenix:6006`, y fijar la versión de Jaeger igual que la de Phoenix (DT-9).


## DT-32 — Agenda (asesor, horarios y reservas): por HTTP en el catálogo, no como tools MCP

**Estado:** aceptada

**Contexto.** `C-2` (#28) pide `assign_advisor`, `get_slots` y `book`. Las necesitan `A-10` (ruteo
por zona) y `B-8` (agendar la visita). Hasta ahora nadie escribía en `visits`, y el lead se asignaba
solo en `core` al mandar la tarjeta de `B-7`. Todo lo que el catálogo publica por MCP, `core` lo
descubre y se lo da al LLM como tool (DT-13). Con `book` y `assign_advisor` por MCP, el modelo podría
reservar visitas o elegir asesor por su cuenta.

**Decisión.**
- **HTTP, no MCP.** Las tres operaciones viven en el Catalog Service como `POST /scheduling/<operación>`
  (`services/catalog/app/scheduling_api.py`). Sus contratos están en `contracts/scheduling/`, no en
  `contracts/mcp/`, así que el servidor MCP sigue publicando solo las 5 tools de lectura. Las llama el
  código de `core`, nunca el modelo. El cuerpo se valida contra el `inputSchema` del contrato, igual que
  en MCP. Los errores usan los códigos de `catalog_error` más `SLOT_TAKEN`, con estado HTTP 404, 422,
  409 y 503.
- **`assign_advisor`** deja escrito `leads.advisor_id` y es idempotente: si el lead ya tiene asesor (por
  ejemplo, el que le asignó `B-7`), devuelve ese. El orden de preferencia es zona e idioma, solo zona,
  solo idioma, y por último cualquiera. Dentro de cada grupo gana quien tiene menos leads asignados,
  con desempate por id: los leads de una zona se reparten por turnos. La escritura es condicional
  (`WHERE advisor_id IS NULL`), así que dos llamadas simultáneas dejan un solo asesor.
- **`get_slots`** calcula los horarios a partir de un horario de atención fijo: lunes a sábado, de 9:00
  a 18:00, en bloques de una hora, hora de Guadalajara (UTC-6). Quita los bloques ya empezados y los que
  tienen una visita. No hay tabla de disponibilidad por asesor.
- **`book`** es idempotente por `idempotency_key`. El reintento se resuelve antes que cualquier otra
  regla: devuelve la misma visita con `created=false`, aunque el horario ya haya pasado. Reusar la clave
  con otros datos es `INVALID_INPUT`. Solo acepta el inicio de un bloque futuro y una propiedad
  `disponible`. La migración 0005 agrega `UNIQUE (advisor_id, scheduled_at)` en `visits`: con dos
  reservas simultáneas, la base deja pasar una y la otra recibe `SLOT_TAKEN`.

**Alternativas.**
- Publicarlas como tools MCP y ocultarlas del LLM en `core`, descartada: obliga a `core` a conocer
  nombres de tools, lo que DT-13 evita.
- Integrar ya un proveedor de calendario real, descartada para esta historia: lo elige `B-8`.
  `get_slots` es el punto donde se conecta: la forma de su respuesta no cambia.
- Guardar la zona horaria como `America/Mexico_City` con `zoneinfo`, descartada: exige la base tzdata
  en la imagen y en Windows. México no tiene horario de verano desde 2022, así que el desfase fijo da el
  mismo resultado.
- Round-robin con un puntero por zona, descartado: necesita estado nuevo. Contar los leads asignados
  reparte igual y además compensa los leads que `B-7` ya asignó.

**Consecuencias.**
- `A-10` pasa a llamar `assign_advisor` en lugar de `pick_advisor`, en
  `services/core/app/channels/notifications.py`. A diferencia de `pick_advisor`, `assign_advisor` no
  exige que el asesor tenga el chat de Telegram registrado: `A-10` decide qué hacer con un asesor que
  todavía no abrió el bot.
- `B-8` usa `get_slots` y `book` con el asesor asignado.
- `book` no cambia `leads.status` ni escribe `funnel_events`: la transición a `visit_scheduled` la sigue
  marcando `core` (DT-17).
- Cancelar o mover una visita queda fuera de esta historia.


## DT-33 — Ruteo del lead al asesor: lo decide la agenda del catálogo, solo entre asesores con Telegram

**Estado:** aceptada

**Contexto.** `A-10` (#12) pide asignar el lead por zona, reparto por turnos e idioma, con una cola
general si no hay asesor en la zona. Hasta ahora lo hacía `pick_advisor` en `core`, al mandar la
tarjeta de `B-7`: el primer asesor registrado de la zona, siempre el mismo, sin idioma ni reparto.
`C-2` ya implementa esa regla en `assign_advisor` (DT-32), pero sin mirar Telegram. Un asesor que nunca
abrió el bot no recibe la tarjeta. Si la agenda pudiera elegirlo, en una demo con un solo asesor
registrado la mitad de los leads calificados quedaría sin aviso.

**Decisión.**
- `notify_advisor` y `notify_handoff` (`services/core/app/channels/notifications.py`) piden el asesor a
  `POST /scheduling/assign_advisor` con `registered_only=true`. Ese parámetro nuevo es opcional en el
  contrato, así que no rompe a nadie. La tarjeta y la ficha de traspaso van al asesor que la agenda
  devuelve, y la agenda deja escrito `leads.advisor_id`.
- Si no hay ningún asesor con Telegram registrado (`NOT_FOUND`), no se asigna a nadie y no se manda
  nada, igual que antes.
- Si la agenda falla por cualquier otro motivo (catálogo caído, respuesta inesperada), se usa la regla
  local `pick_advisor` y queda un aviso en el log. El aviso al asesor no depende de que el catálogo
  responda.
- El idioma es siempre `es`: es el único en que conversa el agente.
- El cliente es `services/core/app/scheduling_client.py`, con `httpx` y la misma `CATALOG_SERVICE_URL`
  que el cliente MCP.

**Alternativas.**
- Llamar a `assign_advisor` sin filtro, descartada por el caso de la demo que describe el contexto.
- Dejar que la agenda reasigne cuando el asesor asignado pierde su chat de Telegram, descartada: rompe
  la garantía de DT-32 de que un lead asignado no cambia de asesor. Ese caso queda en el log.
- Sin respaldo local, con el aviso perdido si el catálogo no responde, descartada: la regla local ya
  existía y estaba probada, y quitarla hace más frágil una parte que hoy funciona.

**Consecuencias.**
- Hay dos lugares con reglas de asignación: la agenda, que es la principal, y `pick_advisor`, el
  respaldo. Si cambia la regla, la que manda es la de la agenda.
- Los tests de `core` no levantan el catálogo. Un fixture de `services/core/tests/conftest.py` simula la
  agenda caída, así que los tests de `B-7` y `A-12` prueban el respaldo. Los de `A-10`
  (`test_advisor_routing.py`) inyectan su propia agenda.

## DT-34 — Inventario de demo sintético, en México, sembrado desde el catálogo

**Estado:** aceptada

**Contexto.** `C-9` (#35) pide un inventario de demo con casos borde a propósito. Hasta ahora el
servicio `seed` solo sembraba asesores, así que el catálogo del stack arrancaba vacío y toda búsqueda
devolvía cero propiedades.

**Decisión.** El catálogo del stack se siembra con 12 propiedades y 3 promociones **ficticias** de
`services/catalog/app/data.py`, en un inventario con zonas de Guadalajara (Providencia, Chapalita,
Zapopan, Andares, Tlaquepaque) y precios en pesos mexicanos. `scripts/seed_catalog.py` las baja a
Postgres; ese archivo es la única fuente y el seed la importa, no la copia. Los asesores siguen
sembrándose con `scripts/seed_advisors.py` (B-5).

**Por qué datos sintéticos y no scraping.** Se evaluó armar el inventario con un script de scraping contra
el sitio de una inmobiliaria de Medellín. Descartado por tres razones, en orden de peso:

1. **La historia pide cosas que un scraping no puede dar.** C-9 exige casos borde *deliberados*: una
   promoción vencida a propósito, un terreno sin recámaras. Un portal vivo esconde lo vencido y no publica
   lo raro, así que esos casos hay que construirlos a mano igual — el scraping solo servía de andamiaje.
2. **Datos personales.** Los avisos reales llevan nombre y teléfono de los asesores: datos personales bajo
   la Ley 1581 de 2012 (habeas data) en Colombia, y la mayoría de los portales prohíbe el scraping en sus
   términos. El paso irreversible es el `git push` — una vez la semilla está en el repo la ve el equipo y
   la organización del curso, y eso no se deshace.
3. **No coincide con el dominio del proyecto.** El catálogo, el guardrail y el FAQ ya hablan mexicano
   (*apartado*, *preventa*, *enganche*, *alberca*, *salón de eventos*). Una semilla de Medellín obligaba a
   renormear precios y zonas y a dejar el sistema internamente inconsistente.

**Por qué México y no Colombia.** Los docs están en español, así que el país no estaba escrito en ningún
lado: era una decisión implícita que se iba a volver a discutir. Se fija México porque el dominio *técnico*
ya es mexicano — el vocabulario cerrado de amenidades de DT-26, las reglas BANT-E de DT-14 y
`REQUIREMENTS` (`down_payment_ratio` 0.10 en venta y 0.20 en preventa, `income_multiple` 3 en renta) están
calibrados contra ese mercado. Cambiar de país no agrega nada a una demo que se evalúa por coherencia
interna, y sí obliga a rehacer trabajo de otras historias.

**Consecuencias.** El proyecto no tiene cliente, así que esto se decide pensando solo en la demo. El seed
es un `INSERT` idempotente sobre la forma de filas ya existente, así que un cliente real con su propio
inventario no obliga a cambiar nada de lo decidido acá.

**Lo que NO quedaría resuelto si el cliente fuera colombiano.** El cambio **no es de vocabulario**:
`zone` es un string que se filtra con `ilike` (`services/catalog/app/service.py`) y `price` se guarda
crudo, sin formateador de moneda en código de producción — son filas nuevas, no código. Lo que sí hay que
recalibrar:

- **El scoring.** Los presupuestos en COP son ~200x más grandes en número que en MXN (2,350,000 MXN ≈
  500,000,000 COP). Las bandas de BANT-E de DT-14 y el umbral `1.5M-2.5M MXN` de
  `services/core/tests/test_scoring.py` están calibrados contra millones de MXN: sin ajustarlos, un lead
  Medellín con presupuesto de 500 millones cae en COLD.
- **La extracción de presupuesto.** El patrón de `services/core/app/fakes/fake_llm.py` matchea
  `mil|millones|k`; allá se dice "mil millones".
- **El vocabulario de amenidades** (DT-26): cuatro sinónimos más, del orden de veinte minutos.
- **El FAQ y el conocimiento de negocio**: normas de financiamiento y contexto del mercado colombiano. Eso
  es contenido, no código.

**Alternativas.** (a) Scraping de un portal público: descartado arriba. (b) Datos sintéticos en Colombia:
descartado por el costo de recalibrar el resto del dominio sin un cliente que lo pida. (c) Datos sintéticos
de un cliente real cuando exista: es lo que se haría si el proyecto deja de ser capstone, y no obliga a
cambiar nada de lo decidido acá.

## DT-35 — El gold set de fair housing prueba el pipeline completo, y la situación familiar queda pineada

**Estado:** aceptada — con 6 de los 22 pares en `xfail(strict=True)` hasta que A-11 corrija el *scoring*

**Contexto.** `C-6` (#32) pide N pares de perfiles idénticos salvo una categoría protegida
introducida incidentalmente, con diferencia de score total = 0 como *gate* de CI. `A-11` (#13) depende
de esta prueba: sin ella no hay forma objetiva de saber que el fair housing del *scoring* funciona, y
`design.md` §5.3 ya markaba que faltaba revisar `decision_maker` (`pareja`/`familia`). La pregunta de
diseño era qué tan profundo probaba el gold set: `compute_score` con señales escritas a mano, o el
pipeline real de un turno.

**Decisión.** El gold set de C-6 (`services/core/tests/test_fair_housing_gold_set.py`) prueba las tres
capas por las que un sesgo puede colarse en el score, no solo la suma: `input_guard.inspect_input().signal_text`
→ el extractor de señales → `lead_scoring.score_turn()`. Son 22 pares de leads idénticos salvo una mención
de categoría protegida, y el criterio de aceptación es diferencia 0 en el total, **por dimensión y también
en la banda** (la banda ordena la cola del panel, `B-6`, y es lo que decide si el lead califica y llega a
un asesor, `B-7`: por debajo de `COLD` 41 no califica).

**Lo que encontró al escribirse.** 16 de los 22 pares dan 0. Los 6 de "con quién compras" no:

| Lo que dice el lead | `decision_maker` | Total | Δ |
| :--- | :--- | ---: | ---: |
| decido yo | `solo` | 100 (HOT) | — |
| con mi esposa / con mi esposo / con mi pareja | `pareja` | 92 (HOT) | −8 |
| con mis padres / con mi familia | `familia` | 88 (HOT) | −12 |
| con mis hijos | `None` → `no_declarado` | 80 (HOT) | −20 |

Los tres siguen en `HOT`: la banda es `COLD` ≤ 40, `WARM` 41-70, `HOT` ≥ 71 (`scoring.py`), y el peor
caso del gold set llega a 80. O sea que **la comparación de banda del gate no muerde con esta fixture** —
no hay ninguna de las 22 fugas que la haga dispararse. Queda como red contra una fuga de 30 puntos o
más, no como detector de estos tres deltas. Los deltas son de Authority, y son igual de reales.

Son dos defectos distintos y por eso se arreglan de forma distinta:

1. **La tabla de puntos.** `guardrails.md` §1 declara "situación familiar" y "estado civil" como categorías
   protegidas y `input_guard._PROTECTED` trata `casad[oa]s?/solter[oa]s?/divorciad[oa]s?` y `tengo hijos|mis
   hijos` como menciones protegidas; después `scoring.DECISION_MAKER_POINTS` le da 8 a `familia` y 12 a
   `pareja` contra 20 de `solo`. El proyecto declara protegida una categoría y la puntúa. Lo arregla A-11.
2. **La redacción.** `"con mis hijos"` → el guardrail hace bien su trabajo y borra la mención → queda
   `"...con"` colgando → el extractor ya no encuentra quién decide → `no_declarado` → 0 de Authority. **Aplanar
   la tabla no arregla este**: el sesgo está en que la redacción destruye una señal legítima. También es de A-11,
   y es una decisión de scoring, no de C-6.

**Por qué los 6 quedan con `xfail(strict=True)` y no con el gate estricto.** Con el gate estricto, C-6
aterrizaría con `main` en rojo hasta que A-11 lo resuelva, y el acuerdo del equipo es nunca mergear en rojo.
Con `strict=True` el CI queda verde, el defecto queda visible con su número, y cuando A-11 lo arregle el
xfail se vuelve XPASS y **el test se pone rojo solo**, obligando a quitar la marca. Aterrizar C-6 no
bloquea a nadie: es A-11 quien lo necesita, y B-8 y la agenda de C-2 no lo esperan.

**Por qué el test del par "con mis hijos" no se puede resolver solo con la tabla.** Se verificó simulando los
dos arreglos posibles: aplanando `DECISION_MAKER_POINTS` los 5 pares de la tabla pasan a 0 y `con mis hijos`
sigue en −20. Queda anotado acá para que A-11 no reciba el PR pensando que el arreglo es de una línea.

**Consecuencias.** (a) El gold set vive en `services/core/tests/`, junto a los de A-5 y A-6, aunque su
contenido sea de C-6: `guardrails.md` §4 ya apunta a esa carpeta para los tres guardrails con gold set.
(b) El extractor de los tests es el determinista (`fakes/fake_llm.py`), no Bedrock — mismo criterio que los
gold sets de A-5 y A-6. Lo que el modelo real haga con una mención protegida no lo prueba este archivo:
queda como verificación en vivo, igual que los guardrails de salida listados en `design.md` §5.2.
(c) `DECISION_MAKER_POINTS` sigue como está. Si alguien aplana la tabla sin quitar los xfail, el CI lo avisa.

**Alternativas.** (a) Gate estricto y `main` en rojo hasta A-11: más honesto en el momento, pero rompe el
acuerdo de no mergear en rojo y deja el PR de C-6 sin poder cerrarse. (b) Gold set solo sobre `compute_score`
con señales escritas a mano: más rápido de escribir, pero no encuentra ninguno de los dos defectos, porque
ninguno de los dos está en la tabla de puntos. (c) No construir el gold set hasta que A-11 esté: A-11 depende
de C-6, y sin la prueba no hay forma objetiva de saber que el arreglo funcionó.

## DT-37 — El umbral de cada gold set vive en su historia, y un registro central los comprueba

**Estado:** aceptada

**Contexto.** `OBS-4` (#46) pide que CI falle si cualquier gold set baja del umbral que define su propia
historia. Al revisar el repo, la mitad de eso ya era cierto y la otra mitad no: cada gold set traía su
`assert len(CASES) >= N` a nivel de módulo, y se verificó que bajarlo deja a `pytest` con exit 2 (error de
colección), o sea que CI se pone rojo. Lo que no existía era la parte de "cualquier":

- Los umbrales estaban escritos a mano dentro de cada archivo, sueltos, sin un lugar donde se vieran juntos.
- Tres de cuatro **no coincidían con su historia**. Solo A-5 pedía un número ("≥30 preguntas"). El gold set
  de C-6 assertaba `>= 20` con un mensaje que afirmaba *"C-6 pide al menos 20 pares"*, y `C-06.md` no pedía
  ningún número: decía "N pares". C-08 y A-07 tampoco definían ninguno, y sus archivos assertaban 15 igual.
  Los tres números los había inventado quien escribió el test.
- Nada obligaba a un gold set **nuevo** a declarar umbral. Sin `assert`, un gold set vacío pasaba en verde
  para siempre.

**Decisión.** El umbral es un dato de la historia, y un registro central lo comprueba:
`services/core/tests/test_gold_sets_gate.py` declara `GOLD_SETS` (historia → módulo → atributo → mínimo) y
verifica tres cosas: que cada gold set tenga al menos los casos que su historia pide, que **todo** `*gold*.py`
de la carpeta esté registrado — si aparece uno nuevo y nadie lo anotó, el CI se cae diciendo cuál — y que el
gate se autocompruebe, pasándole un mínimo imposible y exigiendo que lo detecte. En paralelo, el número que
los archivos afirmaban se escribió en las historias que no lo tenían: `C-06.md` (≥20 pares), `C-08.md` (≥15
casos) y `A-07.md` (≥15 casos, en sus notas de diseño, porque su AC va sobre la tabla de *claims* y no sobre
el conteo).

**El umbral es un conteo, no una cobertura.** Es lo que evita el choque con DT-35: los 6 pares con
`xfail(strict=True)` de C-6 **no** ponen a C-6 "bajo su umbral", porque el umbral de C-6 es el número de
pares (22 ≥ 20) y la diferencia de score = 0 ya la vigila el propio `xfail(strict=True)` — que se vuelve XPASS
*strict* y pone el CI rojo solo cuando A-11 arregle el sesgo. El gate de OBS-4 no duplica ese trabajo ni lo
contradice: mira el conteo.

**C-7 entra tolerado.** La entrada de C-7 (`test_factuality_catalogo_gold_set`) llega con su PR, que todavía
no está en `main`, así que va con `requerido=False`: si el módulo no está, el gate avisa con un warning y sigue
verde; cuando aterrice, la entrada empieza a exigir su umbral en el mismo run, sin que nadie toque el
registro. Se verificó que con `requerido=True` el CI se cae en `main` desde el primer día.

**Consecuencias.** (a) La historia pasó a ser la fuente de verdad del número, que es literalmente lo que OBS-4
pide; el registro es quien lo comprueba, no quien lo decide. (b) La convención de nombres pasó a ser carga
real: la regla de "todo registrado" se apoya en `*gold*.py`. Un gold set con otro nombre no lo encontraría,
por eso el registro es la lista explícita y esa regla es la red, no al revés. (c) `test_promise_guard.py`
(A-7) está registrado aunque no lleve "gold" en el nombre, porque es una suite adversarial con el mismo tipo
de umbral; el registro es un superconjunto de los `*gold*.py`. (d) El `assert` dentro de cada archivo se queda:
es la red local, y el registro no lo reemplaza. Si alguien lo baja, su propio archivo se pone rojo aunque el
registro no lo note. (e) La autocomprobación del gate evita el peor modo de fallo de esto —un gate que no
detecta nada y por lo tanto nunca se cae— y se verificó además con los tres escenarios reales: subir un
umbral, agregar un gold set sin registrar y exigir C-7 antes de que aterrice.

**Alternativas.** (a) Un paso de CI que solo imprimiera los conteos: es lo más chico, pero no agrega ninguna
garantía, porque los números siguen sueltos y nadie obliga a declararlos. (b) Registro central sin exigir
registrar los gold sets nuevos: no rompe a nadie, pero deja abierto justo el hueco que OBS-4 viene a cerrar.
(c) Gate estricto desde el primer día, exigiendo el gold set de C-7: el gate quedaría completo de una, pero
`main` se pondría rojo hasta que C-7 se apruebe y bloquearía el merge de todos. (d) Esperar a que C-7 aterrice
y escribir el registro de una: más limpio, pero deja OBS-4 parada detrás de un PR que no depende de ella.
(e) Gate sobre cobertura de *claims* en vez de conteo: mucho más fuerte, pero varios gold sets prueban
*pipeline* y no tienen una noción de "claim" que contar; habría que inventar esa abstracción para cuatro
archivos que no la piden.

