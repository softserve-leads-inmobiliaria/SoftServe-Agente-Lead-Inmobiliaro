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
  Con el LLM real, el código la confirma (#228, `handoff.asks_for_a_person`): el mensaje tiene que pedir una
  persona ("agent", "someone", "call me"…) o aceptar con un sí corto la oferta de un asesor del turno anterior.
  Si no, el turno sigue como `otro` y se audita `intent.human_request_unconfirmed`. En vivo, el clasificador
  traspasó un mensaje con datos sensibles ya quitados que llegaba después de una oferta de asesor.
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
  puede corregirse consultando de nuevo. La instrucción va en el *system* y no como mensaje `user`, y
  aclara que el lead no la ve (#213): como mensaje `user`, el modelo creía que el lead lo había corregido,
  se disculpaba y prometía buscar sin hacerlo. Si la respuesta regenerada también falla, se degrada a un
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

**Estado:** aceptada. El filtro "solo entre asesores con Telegram" lo reemplazó DT-42 (`B-15`): hoy la agenda
elige entre todos los asesores

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

**Estado:** aceptada; el país y la moneda los reemplaza DT-55 (Colombia, USD). Lo de datos sintéticos y no *scraping* sigue vigente

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

**Estado:** aceptada. Los 6 pares en `xfail(strict=True)` los resolvió A-11 (DT-38): hoy son 22 de 22 en verde

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

---

## DT-36 — El gold set de factualidad se arma sobre la salida real del catálogo, y el contrato se valida aparte de A-4

**Estado:** aceptada

**Contexto.** `C-7` (#33) pide ≥30 preguntas cubriendo el catálogo de demo, con *gate* de CI al 100%. `A-5`
(#7) ya había construido un gold set de 32 casos, pero con los `tool_results` **escritos a mano**. Después
de eso el catálogo migró a Postgres (C-1) y recibió el inventario de demo de 12 propiedades (C-9, #35), así
que la pregunta de diseño era qué aporta C-7 sobre lo que A-5 ya hizo — la historia lo dice: "lo que esta
historia agrega sobre eso es cubrir el catálogo de demo".

**Lo que encontró al escribirse.** Dos cosas, y ninguna era un defecto del guardrail.

1. **Las fixtures de A-5 ya no son la salida de ninguna tool.** Contrastadas contra `contracts/mcp/`: los
   dos `search_properties` les falta `address_approx` (que el contrato exige), el `get_property` les falta
   `type`, `zone` y `address_exact`, y el `get_promotions` le falta `valid_until`. La clave es que **nadie
   lo comprobaba**: el gold set congelaba literales sin contrastarlos con el contrato, así que la deriva
   entre el catálogo y el gold set pasó inadvertida. Si mañana C-1 renombra `m2` o suelta `bathrooms`, el
   gold set de A-5 sigue en verde. (`unavailable` sí es real, pero lo agrega A-4 después, no el catálogo.)
2. **Cobertura:** 7 de las 12 propiedades del inventario no aparecían en **ningún** gold set. Ninguna renta,
   ninguna preventa, ni el estudio, el terreno, el local comercial ni la cerrada — justo los casos borde que
   C-9 metió a propósito.

Al escribir los casos sobre datos reales, tres de los primeros salieron mal, y **los tres eran diseño malo
del caso, no fallas del guardrail**: el precio de una apartada (A-4 mueve a `unavailable` guardando solo
`{id, title, status}` y descarta el precio a propósito, `availability.py:61`); unos "3 baños" que pasaban
porque 3 era el conteo del listado (`factuality.py:59`); y "el estudio tiene 2 recámaras" cuando el estudio
tiene `bedrooms: 0` y el 2 eran los baños de otra propiedad del turno. Los tres son DT-23, el límite ya
documentado de que el guardrail no sabe de qué propiedad habla el texto.

**Decisión.** El gold set de C-7 vive en `services/core/tests/test_factuality_catalogo_gold_set.py`: **75
casos** (46 que deben pasar, 29 que deben bloquear) sobre **27 turnos** que cubren las 12 propiedades y las 5
tools. Sus fixtures las produce `services/core/tests/demo_catalogo.py`, que corre `CatalogService` de verdad
sobre la base migrada que ya monta el `conftest.py` de `core`, sembrada con `scripts/seed_catalog.seed()`
—el mismo del compose, así que el inventario no se duplica en ningún lado.

Cuatro cosas que eso obliga a decidir:

- **Las fixtures se generan en cada corrida, no se commitean.** Un JSON versionado reintroduce exactamente la
  deriva que C-7 viene a cerrar: un archivo que nadie regenera termina dejando de ser la salida del catálogo
  sin que nadie lo note.
- **El catálogo corre en un subproceso.** `core` y `catalog` comparten el nombre de paquete `app` (sus dos
  `conftest.py` lo ponen en `sys.path`, y CI corre cada carpeta en un proceso distinto por eso), así que el
  generador se lanza con `PYTHONPATH=services/catalog` y devuelve el JSON por stdout. Cada proceso ve un solo
  `app`, sin manipular `sys.modules` a mano.
- **La capa de A-4 la pone el guardrail real, no una copia.** `AvailabilityGuard` no habla con el catálogo por
  MCP: solo necesita un objeto con `await call(tool, arguments)`. `demo_catalogo._TurnoGrabado` se lo da
  reproduciendo las salidas de la corrida —incluida la de `get_availability` de cada propiedad—, así que lo
  que ve el guardrail de factualidad es literalmente lo que A-4 haría en producción. La alternativa obvia,
  replicar la transformación de `availability.py` dentro del test, se descartó: una copia de la lógica del
  guardrail queda en verde el día que el guardrail cambia, que es justo el modo de falla que C-7 vino a cerrar.
- **El contrato se valida contra la salida cruda, y la capa de A-4 aparte.** `outputSchema` tiene
  `additionalProperties: false` y no contempla la revalidación, así que la salida cruda es la única validable
  tal cual (27/27 en verde). `Demo` expone las dos vistas de **una sola** corrida —`crudo` y `con_a4`— porque
  `get_availability` estampa su `checked_at` y pedir los payloads dos veces daría dos instantes distintos. La
  forma post-A-4 se fija con su propio test: un `search_properties` es el mismo objeto más
  `availability_checked_at` y `unavailable`; un `get_property`, más `availability_checked_at`; las otras tres
  tools pasan intactas.

**El reloj va fijo en los dos lados, y no es cosmético.** `factuality._walk` extrae números de *cualquier*
string del payload, así que el `checked_at` real del catálogo (`2026-09-28T11:59:35.824340-05:00`) metía
`35.824340` en el set de números autorizados, y los microsegundos cambian en cada corrida: cualquier caso
que rozara ese número haría fallar el gold set al azar. Por eso el generador inyecta `now` fijo a
`CatalogService` (el servicio ya recibe el reloj por inyección), con lo cual el `checked_at` que ve A-4 es
`2026-01-01T00:00:00+00:00`, el mismo valor estable que ya usan `catalog_stub.py` y `test_availability.py`.
El test de A-4 lo comprueba, así que si el reloj dejara de estar inyectado el CI se cae en vez de volver el
gold set aleatorio.

**Cómo se verificó que el gate muerde.** Por mutación: se rompió `factuality.py` de ocho maneras (quitar el
manejo de comas, no contar el tamaño de las listas, ignorar lo que dijo el lead, ignorar los estados, no
examinar los strings, permitir cualquier número, dejarlo pasar todo, bloquearlo todo) y el gold set tiene
que caerse en las ocho. La primera corrida detectó **siete**: faltaban casos donde el estado
("apartado"/"cerrada") *no* está respaldado, porque los que había solo afirmaban estados correctos y pasaban
igual con el chequeo de estados desactivado. Con los ocho casos agregados, las ocho se detectan. Es la forma
en que un gold set se vuelve un test que no comprueba nada: no leyéndolo, sino rompiéndolo.

Un detalle que salió de esa misma prueba y que conviene no perder: la mutación "dejar pasar todo" se lleva
por delante los 29 casos que deben bloquear, y ninguno de los 46 que deben pasar. Es el número exacto de los
29 casos de bloqueo, así que el conteo del gold set queda verificado contra sí mismo.

**Alternativas.** (a) Volcar el JSON real a un archivo commiteado: se ve en el diff y un cambio de catálogo
se nota en review, pero puede separarse del catálogo si nadie lo regenera — reintroduce el problema. (b) Derivar
los `tool_results` de las listas puras de `data.py` replicando la proyección a mano: corre sin Postgres, pero
es literalmente el problema original, dicts a mano sin validar. (c) Cargar `app.data` y `app.service` del
catálogo en el test de `core` con `importlib` y `sys.modules`: todo en un archivo, pero frágil, depende del
fallback de CPython para `from app import data` y se rompe con el primer import de otra cosa. (d) Mover el
gold set a `services/catalog/tests/` y cargar `factuality.py` por ruta (es autónomo, no importa nada local):
invierte lo que fijó DT-35 y rompe el patrón al que `guardrails.md` §4 apunta. (e) Ampliar el `outputSchema`
para admitir los campos de A-4 y validar el payload final de una: más simple, pero toca el contrato de C-1,
fuera del alcance de C-7. (f) Reescribir las 32 fixtures de A-5 sobre el catálogo real: eliminaría la
divergencia de raíz, pero esos 32 casos prueban un criterio que sigue vivo (DT-23) y el criterio del repo es
no borrar casos antiguos; además, el gate de contrato nuevo es lo que habría delatado la deriva. (g) Replicar
la transformación de A-4 dentro del test en vez de ejecutar `AvailabilityGuard`: es lo que se hizo primero y
funcionaba, pero es una segunda copia de `availability.py` que se queda en verde cuando el guardrail cambia,
y el test que debería delatar esa deriva se habría quedado mudo. Salió barato corregirlo porque
`AvailabilityGuard` solo depende del protocolo `call`, no del transporte: no hace falta red para correrlo.

**Consecuencias.** (a) C-7 **no arregla** el gold set de A-5: lo expone. El test de contrato nuevo es el que
se habría caído cuando las fixtures se quedaron atrás, y ahora se queda atento si vuelve a pasar. (b) Los
gold sets de guardrail son cuatro archivos en `services/core/tests/` (A-5, A-6, C-6, C-7), y `demo_catalogo.py`
es el único que necesita Postgres y un subproceso: ~2s a la suite de `core`, que ya lo necesitaba. (c) Los
límites de DT-23 quedan fijados como casos que **pasan** a propósito, con el porqué al lado, no como
`xfail`: el guardrail no está roto, su límite es conocido y la repo no mergea en rojo. (d) A diferencia de C-6,
aquí no hay ningún `xfail(strict=True)`: 75/75 en verde y no queda ningún defecto abierto. (e) El gold set
hereda una limitación de los datos reales, y conviene que quede explícita: una de las cinco mutaciones de
`availability.py` —no revalidar el estado en `get_property`, dejar el que traía el listado— **no se puede
matar con datos reales**, porque el listado y `get_availability` leen la misma columna de Postgres y no pueden
discrepar. Ese caso es justamente el que motivó A-4, así que tampoco es opcional descartarlo:
`test_availability.py::test_get_property_replaces_a_stale_status_with_the_current_one` lo cubre con un
catálogo que miente, que es la única forma de producirlo. Lo que sí se comprueba acá es que el estado que
A-4 estampa venga de `get_availability` y no del listado.

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

**Un gold set cuyo PR sigue abierto entra con `requerido=False`.** Si el módulo no está, el gate avisa con
un warning y sigue verde; cuando llega a `main`, la entrada empieza a exigir su umbral. Así entró C-7
mientras su PR (#117) estaba abierto. Ya en `main`, su entrada pasó a requerida, con el atributo `CASOS`
que usa ese archivo: con `requerido=False` y el nombre `CASES`, los dos PR pasaban por separado y juntos
dejaban `main` en rojo. Al registrar un gold set que todavía no está en `main`, hay que confirmar el nombre
del atributo en su PR.

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

## DT-38 — Authority mide si se dijo quién decide, no con quién; "mis hijos" se generaliza en vez de borrarse

**Estado:** aceptada

**Contexto.** `A-11` (#13) pide que el *scoring* nunca use ni infiera una categoría protegida, con el gold
set de perfiles pareados de `C-6` como *gate* (diferencia de score = 0). Ese gold set (DT-35) encontró dos
defectos en la dimensión Authority, que llegaban a 6 pares con `xfail(strict=True)`:

1. `DECISION_MAKER_POINTS` daba 20 a `solo`, 12 a `pareja` y 8 a `familia`. Decidir en pareja o en familia
   es estado civil y situación familiar, que `guardrails.md` §1 declara protegidos: el proyecto los
   puntuaba (−8 y −12).
2. `"lo hablo con mis hijos"`: el guardrail de entrada borraba `mis hijos`, quedaba `"lo hablo con"` y el
   extractor ya no veía quién decide. El lead caía a `no_declarado` (−20) por tener hijos. Aplanar la tabla
   no lo arreglaba.

**Decisión.**
- **Authority vale lo mismo para cualquier forma de decidir declarada.** `solo`, `pareja` y `familia` dan
  20; `no_declarado`, 0 (`services/core/app/scoring.py`, tabla de `design.md` §3). La dimensión mide si el
  lead dijo cómo se toma la decisión, que es lo que le sirve al asesor, y no con quién vive.
- **`mis hijos` / `mis hijas` se generaliza a `mi familia` en vez de borrarse**
  (`input_guard.strip_protected`, `_FAMILY_ROLE`). El texto que ven el LLM y el historial ya no dice que
  el lead tiene hijos, pero conserva que decide en familia. El resto de las menciones protegidas se sigue
  borrando como antes (DT-15). `tengo hijos` también se borra: no dice nada de quién decide.
- **El contrato no cambia.** `ScoreSignal.decision_maker` sigue siendo `solo`/`pareja`/`familia`/`no_declarado`:
  quitar los valores es un cambio de contrato que toca a Bedrock, a los datos ya guardados y al panel, y
  queda en #119. Mientras tanto la distinción se guarda pero no pesa en el score.
- Los 6 pares de C-6 pierden el `xfail`. El gold set queda en 22 de 22 en verde.

**Alternativas.**
- Bajar `solo` a 12 para igualar: castiga al que decide solo sin ninguna razón de negocio. Descartada.
- Borrar también `con mi esposa` y `con mis padres`: igualaba por abajo, con todos en `no_declarado`, y
  el asesor perdía un dato útil. Descartada.
- Reemplazar `mis hijos` por `[dato omitido]`: el extractor tampoco sabe leer quién decide ahí, así que
  el sesgo seguía. Descartada.
- Colapsar ya el enum a `declarado`/`no_declarado`: es lo más limpio, pero es un cambio de contrato que el
  equipo tiene que acordar. Queda en #119.

**Consecuencias.**
- Un lead que decide en pareja o en familia sube 8 o 12 puntos. Puede pasar de `COLD` a `WARM` o de
  `WARM` a `HOT` y llegar antes a un asesor (`B-7`). Es el efecto buscado: antes llegaba más tarde por su
  situación familiar.
- `test_input_guard.py` y `test_input_guard_turn.py` comparan ahora `con mis hijos` contra `con mis
  padres`, que es el par correcto: mismo quién decide, distinta situación familiar. El par anterior
  (`con mis hijos` contra no decir nada) daba igual solo porque la mención se borraba y se llevaba la
  señal con ella.
- La aserción de banda del gold set sigue sin morder: el control está en 100 y todos los pares también
  (DT-35). Una fuga chica se ve en el total y en la dimensión, no en la banda.

## DT-39 — Agendar la visita: la agenda del catálogo es el proveedor, y el código ofrece horarios numerados

**Estado:** aceptada. Desde DT-42 (`B-15`), no tener asesor con Telegram ya no lleva al respaldo de horario
preferido: la agenda asigna un asesor aunque no tenga Telegram

**Contexto.** `B-8` (#23) pide agendar la visita desde la conversación, con `get_slots` y `book` contra el
asesor del lead, y un respaldo de "hora preferida" si el calendario no responde. DT-32 dejó a esta historia
elegir el proveedor de calendario. Hasta ahora `SCHEDULE -> HANDOFF` lo disparaba la intención
`agendar_visita` sin reservar nada, y `last_tool_results` se sobrescribía cada turno, así que la propiedad
que el lead miró (por ejemplo, la del anuncio, #122) ya no se conocía cuando pedía la visita.

**Decisión.**
- **El proveedor es la agenda del catálogo** (`get_slots` y `book`, DT-32): horario fijo de lunes a sábado,
  de 9:00 a 18:00, y las visitas en `visits`. No se integra un calendario externo.
- **Lo decide el código, no el LLM** (`services/core/app/visits.py`). Al pedir la visita en `RECOMMEND`,
  `core` pide el asesor con `assign_advisor(registered_only=true)`, como DT-33, y le ofrece al lead los
  3 primeros horarios libres de los próximos 7 días, numerados. La oferta queda en `last_tool_results`
  (`visit_offer`). En `SCHEDULE`, la respuesta del lead se lee con reglas: el número ("2", "la 2"), el
  ordinal ("la segunda") o la hora en 24 horas ("16:00", "a las 16"). Si no elige exactamente uno, se le vuelven a
  ofrecer. La respuesta de la agenda reemplaza la del agente en esos turnos.
- **`SCHEDULE -> HANDOFF` lo dispara la visita resuelta**, no una intención (`trigger_for(...,
  visit_settled=True)`). Resuelta es reservada con `book`, o con el horario preferido del lead. Una
  reserva pasa `Lead.status` a `visit_scheduled`. El turno que la resuelve reinicia la racha de
  frustración: "la 2" cae en `otro`.
- **`book` va con `idempotency_key = <conversation_id>:<horario>`**: el reintento de un webhook o un "la 2"
  repetido devuelven la misma visita. Con `SLOT_TAKEN` se ofrecen horarios nuevos.
- **Respaldo de horario preferido.** Si la agenda no responde, no hay asesor con Telegram, no quedan
  horarios, `book` falla por otro motivo o no se sabe qué propiedad visitar, se le pide al lead su día y
  hora preferidos. El siguiente mensaje que mencione un día u hora ("mañana en la tarde", "el sábado") se
  pasa al asesor en la ficha ("Horario preferido para la visita: ..."), con el motivo `visit_requested`.
  Un "ok" no cuenta: se vuelve a preguntar.
- **La propiedad de interés se arrastra** en `last_tool_results` (`property_of_interest`: id, título y
  zona, sin `status`) mientras ningún turno traiga otra. El guardrail de factualidad (A-5) no la ve: usa
  solo las tools del turno en curso. La tarjeta y la ficha del asesor ahora la muestran aunque el turno que
  las dispara no la haya consultado.
- La visita se muestra en la ficha en hora de Guadalajara.

**Alternativas.**
- Integrar Google Calendar u otro proveedor externo, descartada: necesita credenciales y cuentas de los
  asesores y multiplica el esfuerzo de la historia. `get_slots` sigue siendo el punto donde se conectaría.
- Que el LLM entienda la fecha en texto libre ("el martes en la tarde") y el código la valide contra
  `get_slots`, descartada por ahora: depende del modelo y obliga a cambiar el prompt y el contrato de
  clasificación. Las opciones numeradas son deterministas y se prueban sin Bedrock.
- Publicar `get_slots` y `book` como tools del LLM, descartada por DT-32.
- Guardar la propiedad de interés en una columna nueva, descartada para esta historia: necesita una
  migración, y `N-4` (#144) va a guardar el interés por propiedad en su propia tabla.

**Consecuencias.**
- El camino feliz necesita un mensaje más en `SCHEDULE`. Los tests y el guion del arnés
  `flujo_completo_hasta_handoff` responden con un horario. En los tests de `core` la agenda está caída
  (`conftest.py`), así que toman el respaldo; la reserva real se prueba en `test_visits_turn.py` con una
  agenda falsa que valida cada pedido contra el contrato.
- Un lead en `SCHEDULE` que pregunta otra cosa recibe de nuevo los horarios, no la respuesta del agente.
  Si insiste tres veces sin elegir, la racha de frustración lo pasa a un asesor (A-13).
- Cancelar o mover una visita sigue fuera de alcance (DT-32).

## DT-40 — `decision_maker` solo dice si se declaró cómo se decide; "mi esposa" se generaliza a "mi pareja"

**Estado:** aceptada

**Contexto.** DT-38 igualó los puntos de Authority, pero dejó dos huecos (#119). El contrato
`ScoreSignal.decision_maker` seguía siendo `solo`/`pareja`/`familia`/`no_declarado`: el prompt de Bedrock le
pedía al modelo distinguir pareja de familia, y el dato quedaba guardado en `fsm_context.score_signal` y
en las razones de `lead_scores`. Y "con mi esposa" o "con mi esposo" llegaban intactos al LLM y al
historial, aunque revelan el estado civil, que `guardrails.md` §1 declara protegido.

**Decisión.**
- **El enum pasa a `declarado`/`no_declarado`** (`contracts/fsm_state.schema.json`). Authority sigue dando
  20 y 0 (`scoring.DECISION_MAKER_POINTS`). El prompt del clasificador pide `declarado` si el mensaje dice
  cómo se toma la decisión, solo o con otras personas, y le dice al modelo que no distinga con quién.
- **La migración `0006` reescribe lo ya guardado**: `solo`, `pareja` y `familia` pasan a `declarado` en
  `fsm_context.score_signal` y en la razón de Authority de `lead_scores.reasons`. Sin ella, `compute_score`
  rechazaría la señal acumulada de una conversación vieja. El `downgrade` deja `solo`, que valía lo mismo.
- **"mi esposa", "mi esposo", "mi marido", "mi mujer", "mi novia" y "mi novio" se generalizan a "mi
  pareja"**, con el mismo mecanismo que "mis hijos" → "mi familia" (DT-38; `input_guard._GENERALIZED`), y
  se marcan como mención protegida. El LLM sigue viendo que la decisión se comparte.
- El gold set de `C-6` suma "con mi marido" y "con mi novia" (27 pares) y prueba que cada mención que
  revela estado civil o situación familiar llega generalizada al LLM.

**Alternativas.**
- `solo`/`compartida`/`no_declarado`: conserva para el asesor si el lead decide solo, pero sigue guardando
  un dato del hogar que no pesa en el score. Descartada.
- Dejar el contrato como estaba y solo redactar "mi esposa": la distinción seguía guardándose y bastaba
  un cambio en la tabla de puntos para volver a puntuarla. Descartada.
- Tolerar en el código los valores viejos en vez de migrarlos: dejaba guardado justo el dato que se
  quiere quitar. Descartada.

**Consecuencias.**
- El asesor ya no ve en la razón de Authority si el lead decide solo, en pareja o en familia. Si le
  importa, lo pregunta en la llamada.
- "mis padres" no se generaliza: dice con quién se decide, no el estado civil ni si el lead tiene hijos.
  Con el enum colapsado, tampoco se guarda.
- Los ejemplos de `contracts/examples/` y los tests usan `declarado`. Un consumidor que mande `pareja`
  ahora falla la validación del contrato.

## DT-41 — Un cierre es del bot si el lead tocó esa propiedad con el bot en los 60 días previos; las métricas agregadas las ve cualquier asesor

**Estado:** propuesta (`N-2`, #142). Pasa a aceptada cuando la revisen las tres personas del equipo.

**Contexto.** La épica E6 (`docs/DESIGN/propuestas.md` §2) mide cuánto negocio trae el agente. `N-7`
cuenta los cierres atribuidos al bot y `N-8` los muestra en el panel, pero las dos dependen de tres
cosas sin decidir: qué cuenta como "cerrada gracias al bot", quién ve las métricas de toda la
inmobiliaria y qué no puede salir nunca en una vista agregada.

Hay un dato que pesa en la primera: **todo lead de la base llegó por el bot**. `leads` solo se crea desde
Telegram, y `N-3` cierra la operación desde la ficha de un lead. Por eso, "el lead conversó con el bot" es
cierto para el 100 % de los cierres y no mide nada.

**Decisión.**

1. **Regla de atribución.** Un cierre (`deals`, `N-1`) se atribuye al bot si `property_interest` (`N-4`)
   tiene una fila con **el mismo lead y la misma propiedad** del cierre, de tipo `anuncio`, `consulta` o
   `visita`, con fecha **anterior al cierre y como máximo 60 días antes**.
   - `anuncio`: el lead entró por el anuncio de esa propiedad (`?start=prop_…`).
   - `consulta`: el bot la consultó con `get_property` para el lead.
   - `visita`: el bot agendó la visita (B-8).
   - `busqueda` **no cuenta sola**: que la propiedad haya salido en una lista de resultados no muestra
     interés en ella. Sí cuenta si, además, el lead la consultó o la visitó.
   - Haber conversado sin tocar esa propiedad no cuenta (ver el contexto).
   - La ventana se mide desde la **última** interacción que cuenta. Un lead que consultó la propiedad
     hace 90 días y la visitó hace 10 cuenta.
   - Es binaria: un cierre es del bot o no. No se reparte el crédito entre canales.
2. **Quién ve las métricas agregadas.** El mismo criterio de B-9: **cualquier token de asesor** ve las
   métricas de toda la inmobiliaria, con cortes por zona y por asesor (`services/panel/app/funnel.py`).
   No hay token ni rol de gerencia, así que `N-8` no necesita columna ni migración. El resto del panel
   sigue aislado por zona (DT-5): la ficha y la cola de un lead solo las ve el asesor de su zona.
3. **Lo que nunca sale en una vista agregada** (`N-6`, `N-7`, `N-8` y cualquier reporte que venga después):
   - el texto de la conversación (`messages.content`) y los resultados de tools guardados en el contexto
     de la FSM;
   - el teléfono (`leads.phone`) y el correo (`leads.email`);
   - el nombre del lead, si alguna vez se guarda, y su identidad de Telegram (`telegram_user_id`, el
     `@usuario`);
   - el `lead_id` o cualquier enlace a la ficha de un lead. Una vista agregada cuenta leads, no los
     lista. La lista de estancadas de `N-8` muestra propiedades, no leads.

   El detalle de un lead sigue solo en su ficha, para el asesor de su zona.

**Alternativas.**
- **Contar cualquier conversación con el bot.** Descartada: con todos los leads llegados por Telegram,
  da el 100 % siempre.
- **Contar también `busqueda`.** Descartada: atribuiría al bot una propiedad que el lead quizá ni
  abrió, solo porque salió entre tres resultados. Si el equipo la quiere, puede ir como una segunda
  cifra ("cierres asistidos") en `N-7`, nunca sumada a la principal.
- **Ventana de 30 o de 90 días.** 30 deja fuera las ventas, que tardan más que una renta; 90 atribuye
  cierres que ya no tienen que ver con la conversación. 60 es el valor de `propuestas.md` §2.3. La
  ventana es una constante de `N-7` y se puede cambiar sin migración.
- **Token o rol de gerencia.** Descartado por ahora: necesita una columna o tabla nueva, su rotación y
  una forma de entregarlo, para tres asesores que ya ven el funnel global. Se retoma si el equipo de
  asesores crece o si la gerencia es una persona distinta de los asesores.

**Consecuencias.**
- `N-7` implementa la regla tal cual: un `EXISTS` sobre `property_interest` con lead, propiedad, tipo
  y ventana, con la ventana de 60 días en una constante. Los tests de `N-7` cubren los bordes:
  día 60 contra 61, solo `busqueda`, otra propiedad del mismo lead e interacción posterior al cierre.
- La regla depende de que `N-4` registre las interacciones. Los cierres anteriores a `N-4` no tienen
  interacciones registradas y salen como no atribuidos. La historia sintética de `N-9` las siembra
  junto con sus cierres.
- `N-8` usa la misma autenticación que el funnel de B-9. Un token que no existe responde 404.
- Un asesor ve cuántos cierres hizo cada uno de sus compañeros. B-9 ya muestra el funnel por asesor,
  así que no cambia lo que se expone hoy.
- No se aplica un umbral mínimo de conteo por celda. Con tres asesores y cortes por zona, un "1" puede
  dejar adivinar a qué lead se refiere, pero quien lo ve es un asesor de la misma inmobiliaria. Si las
  métricas llegan a salir del equipo (por ejemplo, el reporte semanal por Telegram de
  `propuestas.md` §3), hay que revisarlo.

## DT-42 — Los avisos al asesor se guardan en el panel; Telegram es un canal adicional y el ruteo ya no lo exige

**Estado:** aceptada. Reemplaza el filtro de Telegram de DT-33; el resto de DT-33 sigue igual.

**Contexto.** DT-33 ruteaba los leads solo entre asesores que habían abierto el bot de Telegram
(`registered_only=true`). La tarjeta del lead calificado (B-7) y la ficha de traspaso (A-12) solo existían
como mensajes de Telegram. El equipo no tiene una cuenta de Telegram por asesor, así que sin asesores
registrados pasaban tres cosas: el lead quedaba sin asesor, los avisos no llegaban a nadie y B-8 nunca
podía reservar una visita, porque sin asesor no hay horarios (DT-39).

**Decisión.**
- **El ruteo no exige Telegram.** `notify_advisor`, `notify_handoff` y `visits.start` llaman a
  `assign_advisor` sin `registered_only`. La agenda elige entre todos los asesores, con la misma
  preferencia de zona, idioma y reparto por turnos. La regla local `pick_advisor` tampoco filtra.
  `registered_only` sigue en el contrato, opcional, pero `core` ya no lo usa.
- **Cada aviso se guarda en `advisor_notifications`** (migración `0007`): asesor, lead, tipo
  (`calificado` o `traspaso`), texto, fecha y fecha de lectura. El texto es el resumen de la tarjeta o de
  la ficha (motivo, score, propiedad, visita u horario preferido), sin la transcripción, que ya está en la
  ficha del lead.
- **Telegram es un canal adicional.** Si el asesor registró su chat, además se le manda el aviso por ahí,
  como antes. Si no lo registró, el bot no está arrancado o el envío falla, el aviso queda solo en el
  panel.
- **Sección "Avisos" en el panel** (`/panel/<token>/avisos`): los últimos 50 avisos del asesor, los más
  nuevos primero, con el contador de no leídos (también en la lista de leads). Abrir uno lo marca como
  leído y lleva a la ficha (303). La página se recarga cada 30 segundos. El aviso de otro asesor responde
  404, igual que uno inexistente (DT-5).
- **El asesor ve también los leads que tiene asignados**, no solo los de su zona. La cola general
  (`assign_advisor`, grupo `any`) le puede asignar un lead de otra zona o sin zona, y su aviso tiene que
  llevar a una ficha que abra. Aplica a la lista, la ficha y "Marcar en gestión".

**Alternativas.**
- Una sola cuenta de Telegram compartida por los asesores: `telegram_chat_id` es único y registrar un
  chat se lo quita a otro asesor, así que solo uno recibe avisos. Descartada.
- Notificaciones push del navegador o *server-sent events*: llegan sin recargar, pero requieren permisos
  del navegador y una conexión abierta por asesor. Recargar cada 30 segundos alcanza para el volumen del
  curso. Descartada por ahora.
- Correo al asesor: necesita un servidor SMTP y credenciales. Descartada.

**Consecuencias.**
- Sin Telegram, el asesor no recibe la notificación push en el celular: tiene que tener el panel
  abierto.
- En la demo basta una cuenta de Telegram, la del lead: los asesores trabajan desde el panel, y B-8
  reserva visitas reales.
- Si un asesor registra Telegram, recibe cada aviso dos veces: en el panel y en el chat. El panel es la
  fuente de verdad, y el contador de no leídos solo baja al abrir el aviso en el panel.
- La migración de `N-1` pasa a `0008`.

## DT-43 — La corrida real de OBS-6 se comprueba en los 3 servicios, con ids por corrida y fuera de CI

**Estado:** aceptada

**Contexto.** `OBS-6` (#48) pide al menos una corrida real, no mockeada, contra los 3 servicios. Los 8
guiones solo se corrían dentro de `tests/harness`, sobre bases temporales, y contra el stack de Docker
`docker compose run harness` solo miraba las respuestas de `core`. Nada comprobaba que lo que el lead
dijo llegara al `panel`. Al preparar la corrida aparecieron los bugs que las notas de la historia
anticipaban: `run_all.py` abortaba sin informe si una respuesta salía del contrato o no era JSON, el
timeout de 30 s no alcanzaba para un turno de Bedrock con cola y reintentos (15 s + 20 s), y los ids
deterministas hacían que una segunda corrida sobre la misma base heredara conversaciones terminadas.

**Decisión.**
- `harness/scripts/live_run.py` reproduce los guiones contra el stack levantado y comprueba los tres
  servicios: las respuestas de `core` (que consulta a `catalog` por MCP), el estado final en Postgres y,
  para cada lead con zona, la cola y la ficha del asesor de esa zona en el `panel`.
- Los ids de la corrida real se derivan de un `run_id` (`build_events(..., run_id)`). Sin `run_id` el
  arnés sigue siendo determinista, como pide CT-9.
- El estado final esperado de cada guion vive en `harness/scripts/expectations.py`, que usan
  `test_e2e.py` y `live_run.py`.
- `--sin-frases` deja de exigir `expect_reply_contains` para la corrida con Bedrock, pero mantiene el
  contrato, la respuesta no vacía, el estado final y el panel.
- **La corrida real no corre en CI.** Necesita el stack de Docker, el bot y cupo del LLM. CI sí corre la
  herramienta contra procesos reales de los 3 servicios (`tests/harness/test_live_run.py`), así que un
  bug como los de arriba se ve en el PR y no el día de la demo.
- Telegram se prueba a mano con un checklist (`docs/TESTING.md`): un bot no puede escribirse a sí mismo.

**Alternativas.**
- Leer el estado desde el `panel` en vez de Postgres: los leads sin zona no aparecen en ninguna cola,
  así que se perdería la mitad de los guiones. Descartada; el panel se comprueba solo donde aplica.
- Automatizar Telegram con una cuenta de usuario (MTProto): pide credenciales personales en el repo
  para una sola comprobación. Descartada.
- Hacer más tolerantes los `expect_reply_contains` de los guiones para Bedrock (sinónimos, regex):
  rompe el determinismo con el agente *fake* sin garantizar nada con el modelo. Descartada.

**Consecuencias.**
- Los leads de cada corrida quedan en la base local y se ven en el panel junto a los de una demo.
  `bash scripts/demo/down.sh` los borra.
- Un guion nuevo tiene que declarar su estado final en `expectations.py`, como antes en `test_e2e.py`.
- La corrida con Bedrock queda pendiente hasta que haya credenciales. Es el mismo comando con
  `--sin-frases`.
- La primera corrida real (2-oct) encontró dos cosas que los tests no veían. El guion
  `deep_link_solo_precio` apuntaba a `prop_00450`, que no existe en el inventario sembrado: `core` lo
  registraba como aviso y seguía, así que el guion pasaba sin probar el deep-link (#122). Ahora usa
  `prop_00233`. Por Telegram, una falla de red transitoria al enviar la respuesta a `STOP` dejó al lead
  sin respuesta, aunque la baja se guardó: el canal no reintenta el envío. Queda como hallazgo para B-1.

## DT-44 — *Scheduler* dentro de `core` con los *jobs* en Postgres; seguimientos antes de `no_response`

**Estado:** propuesta

**Contexto.** `A-17` pide un *scheduler* que dispare las aristas `no_response` (`DISCOVERY -> NURTURE ->
LOST`, design.md §2), que cancele los *jobs* del lead con `/stop` y que reintente un *job* fallido sin
ejecutar ninguno dos veces. DT-3 ya había decidido un *scheduler* embebido, sin Redis. Faltaba decidir
dónde se guardan los *jobs*, cómo se evita la doble ejecución y qué significa "sin respuesta tras N
intentos". También es la base de `B-13`, `A-18` y `N-10`.

**Decisión.**
- **Dónde corre:** dentro de `core`, como dice DT-3. Es un bucle en el `lifespan` que cada
  `SCHEDULER_POLL_SECONDS` (30 por defecto; 0 lo apaga) llama a `Scheduler.run_due` en un hilo. Envía por
  el mismo bot de Telegram de `core`. La primera pasada espera un intervalo, así que arrancar `core` no
  dispara nada.
- **Dónde viven los *jobs*:** en la tabla `scheduled_jobs` (migración `0009`). Cada *job* tiene clase,
  `dedupe_key`, `run_at`, `payload`, estado e intentos fallidos. Un índice único parcial impide dos
  pendientes con la misma clase y clave. Una historia que agregue una clase la suma a `JOB_KINDS` con su
  migración.
- **Una sola ejecución:** el *job* se toma con `FOR UPDATE SKIP LOCKED`, y su efecto y su `done` van en
  la misma transacción. Dos *schedulers* a la vez nunca toman el mismo *job*.
  - El mensaje al lead se envía al final, justo antes del commit. Si el commit falla después de un envío
    exitoso, el reintento repite el mensaje: es el único duplicado posible, y se acepta.
  - Al bloquear la conversación se usa `NOWAIT`. Un turno en curso tiene la conversación y espera al
    *job* para cancelarlo, así que esperar sería un *deadlock*. Si la conversación está ocupada, el *job*
    queda para la próxima pasada sin contar como fallo.
- **Reintentos:** un *job* que falla revierte todo y se reintenta a 1 y a 5 minutos. Al tercer fallo queda
  en `failed`, con el tipo de la excepción en `last_error` (nunca el texto, que podría traer datos del
  lead) y un `error` en el log.
- **"Sin respuesta tras N intentos":** cada intento es un seguimiento al lead.
  - En `DISCOVERY`, con 24 horas de silencio el bot envía un seguimiento fijo, sin LLM, que termina
    recordando cómo darse de baja. Tras 2 seguimientos sin respuesta, la conversación pasa a `NURTURE`.
  - En `NURTURE` se repite lo mismo cada 7 días, y la conversación termina en `LOST`.
  - Cada mensaje del lead cancela el pendiente y reinicia la cuenta, en la transacción del turno.
  - Los tiempos y N se cambian con `SCHEDULER_DISCOVERY_HOURS`, `SCHEDULER_NURTURE_DAYS` y
    `SCHEDULER_FOLLOW_UPS`.
  - Un lead sin chat de Telegram (los del arnés) no recibe nada, pero el intento cuenta.
- **`/stop`:** las dos bajas, la del canal (`consent.withdraw_consent`) y la de `core`
  (`StateStore.withdraw_consent`), cancelan todo lo pendiente del lead. Además, el *job* revisa el
  consentimiento y el estado de la conversación antes de actuar. Si cambió, se cancela.

**Alternativas.**
- Un proceso aparte en el *compose*: necesitaría un segundo cliente del bot con el mismo token, y DT-3
  ya lo descartó para este volumen. Descartada.
- APScheduler con su *job store* en SQLAlchemy: no conoce la transacción del turno ni la conversación
  bloqueada. Habría que envolverlo para lo mismo que hace `SKIP LOCKED`. Descartada.
- `pg_cron` o `LISTEN/NOTIFY`: `pg_cron` no está en la imagen de pgvector, y `LISTEN` no funciona a
  través de un *pooler* en modo transacción, como el de Supabase. Una tabla con `SKIP LOCKED` funciona
  igual en local y en una base administrada. Descartada.
- Mover la FSM tras X tiempo, sin escribirle al lead: no reactiva a nadie, y design.md habla de
  "intentos". Descartada.

**Consecuencias.**
- `NURTURE` y `LOST` empiezan a usarse: el funnel del panel los cuenta desde la primera transición.
- Los leads del arnés en la base local también avanzan solos a `NURTURE` y `LOST` con el tiempo.
- Pasar a `LOST` no cambia `leads.status`: cerrar al lead sigue siendo decisión del asesor.
- Las conversaciones que se quedan en silencio en `QUALIFY`, `RECOMMEND` o `SCHEDULE` no tienen
  seguimiento: la FSM no tiene arista `no_response` ahí.
- Pendiente de verificar en vivo con Telegram, con tiempos cortos (`SCHEDULER_DISCOVERY_HOURS=0.01`).

## DT-45 — Alertas de propiedades nuevas: pedido por regla, "nueva" según el historial de estados

**Estado:** propuesta

**Contexto.** `A-18` pide que el lead pueda decir "avísame si sale algo así" después de una búsqueda con
pocos resultados, y que un *job* de A-17 le escriba una vez por cada propiedad que cumpla. Hay que
decidir cuatro cosas:
- cómo se detecta el pedido;
- qué filtros se guardan;
- qué es una propiedad "nueva";
- cómo pasa el mensaje por los guardrails de salida si no lo escribe el LLM.

**Decisión.**
- **El pedido lo detecta el código**, con una regla como la de `/stop` ("avísame", "me avisas",
  "notifícame", "alerta"), no una intención nueva del LLM. Funciona igual con el agente *fake* y con
  Bedrock, y no cambia el contrato de intenciones ni los gold sets.
- **Se guardan los filtros de la búsqueda**, no el texto. Son los argumentos de `search_properties` del
  turno, o los del turno anterior si ahí se ofreció la alerta (`alert_offer` en `last_tool_results`). Una
  búsqueda sin ningún filtro no se vuelve alerta.
- **Oferta:** si una búsqueda con filtros trae 2 propiedades o menos, el turno agrega una línea ofreciendo
  la alerta. Sin eso, casi nadie sabría que existe.
  - Se ofrece una sola vez por conversación (`alert_offered` en `last_tool_results`, #213): en vivo salía
    en casi cada turno. Las búsquedas siguientes con pocos resultados igual guardan sus filtros, así que
    "notify me" sigue creando la alerta con la última.
  - Cuenta la primera búsqueda del turno, que es la que pidió el lead. Si no trae nada, el agente la repite
    con un filtro menos para ofrecer alternativas (#213), y la alerta tiene que ser de lo que se buscaba.
- **Vigencia:** una alerta activa por lead, que reemplaza a la anterior y vence a los 90 días. `/stop` la
  desactiva por los dos caminos de la baja.
- **"Nueva" significa que entró a `disponible` después de crearse la alerta**, según
  `property_status_events` (N-1): un alta o una vuelta desde `apartado`. Lo que el lead ya vio en su
  búsqueda no le llega como novedad. Los criterios de coincidencia son los de `search_properties`: zona
  por coincidencia parcial, tipo, recámaras exactas y precio dentro del rango.
- **Dos clases de *job* (migración `0011`):**
  - `alert_scan` busca coincidencias cada `ALERT_SCAN_MINUTES` (10 por defecto). Solo existe mientras
    haya alguna alerta activa: se crea con la primera y deja de reprogramarse cuando no queda ninguna.
  - Por cada coincidencia encola un `property_alert`, con la clave `alerta:propiedad` y `once=True`
    (DT-44).
- **El aviso usa una plantilla con datos del catálogo** (título, referencia, zona, tipo, recámaras y
  precio), sin LLM. Aun así pasa por los tres guardrails de salida: factualidad, dirección y promesas.
  Si alguno lo bloquea, no se envía y queda un `warning`. Antes de enviar se revisa otra vez el
  consentimiento y que la propiedad siga `disponible`.
- **El aviso deja la propiedad como la de interés de la conversación** (`property_of_interest`). Así, si
  el lead contesta "me interesa" o pide una visita, el turno siguiente sabe de cuál habla. Si el lead
  estaba en `NURTURE`, su respuesta sigue el flujo normal (`NURTURE -> DISCOVERY`).

**Alternativas.**
- Intención `pedir_alerta` en el clasificador: cambia el contrato, el agente *fake* y los gold sets por
  una frase que una regla reconoce bien. Descartada.
- Comparar contra lo que había en el catálogo al crear la alerta (una "foto" de ids): no ve una propiedad
  que vuelve de `apartado` y guarda una lista por alerta. El historial de estados ya existe. Descartada.
- Disparar la alerta en el momento del alta (en `record_listing`): ata `db/` a `core` y al bot, y el alta
  no siempre pasa por `core`. Descartada: el *scan* periódico desacopla a quien publica de quien avisa.

**Consecuencias.**
- Hoy ningún código de producción da de alta propiedades. El inventario sale del *seed*, que no deja
  eventos, y la historia sintética de N-9 escribe la suya con `record_past`; sus eventos no cuentan para
  las alertas (DT-49). Quien agregue el alta de propiedades tiene que pasar por `db/property_history.py`,
  o las alertas no la verán.
- Con el agente *fake*, que busca sin filtros, la alerta nunca se ofrece; se ve con Bedrock.
- Pendiente de verificar en vivo: buscar algo que no exista por Telegram, pedir la alerta y publicar una
  propiedad con `record_listing`.

## DT-46 — Resumen del traspaso: se genera en el turno, se valida como una respuesta y se guarda en `leads`

**Estado:** propuesta

**Contexto.** `A-16` pide un resumen de la conversación (qué busca el lead, sus objeciones y el siguiente
paso) en la ficha del panel y en la ficha de traspaso, generado al llegar a `HANDOFF` o
`HUMAN_HANDOFF`. Hay que decidir cuándo se genera, dónde se guarda y cómo se valida un texto que lee el
asesor y no el lead.

**Decisión.**
- **Se genera en el mismo turno del traspaso**, después del commit y antes de la ficha (A-12), que lo
  lleva. Así se genera también por el arnés (`POST /events`), que no notifica a nadie. El turno espera
  como mucho `SUMMARY_TIMEOUT_SECONDS` (15 s): pasado ese tiempo, la ficha sale sin resumen.
- **Con Bedrock lo redacta el modelo de conversación**, sin tools y con temperatura 0. Recibe las últimas
  60 líneas de la transcripción guardada y los datos del lead (motivo del traspaso, zona, presupuesto,
  plazo, propiedad de interés, visita), y devuelve un JSON con las tres secciones. **Con el agente
  *fake*** sale de una plantilla determinista con esos mismos datos; las objeciones quedan "sin
  detectar", porque sin LLM no se identifican.
- **Se valida como una respuesta al lead:** factualidad (A-5), dirección reservada (A-6) y promesas
  (A-7), con la transcripción y los datos del lead como fuente válida. La transcripción ya está sin PII ni
  categorías protegidas (A-8), y el agente ya pasó sus propios guardrails. Además, el resumen no puede
  mencionar ninguna categoría protegida: las reglas de A-8, más las formas en tercera persona ("su
  esposa", "sus hijos"). "Su pareja" y "su familia" sí valen, porque son las formas neutras de DT-40.
- **Lo que no pasa se descarta**, sin regenerar: el resumen es un extra y la ficha ya tiene la
  transcripción. Si el LLM falla, tarda demasiado o el JSON viene incompleto, la ficha sale sin resumen,
  como antes, y queda un `warning` con la clase del error o de la violación, nunca el texto.
- **Se guarda en `leads.handoff_summary` y `handoff_summary_at`** (migración `0012`), y no en
  `conversations`. `conversations` es la proyección congelada del contrato
  `fsm_state.schema.json#/$defs/Conversation` (CT-3, `additionalProperties: false`), y la ficha del panel
  es por lead. Guarda el resumen del último traspaso: uno nuevo reemplaza al anterior, y uno fallido no
  lo borra.

**Alternativas.**
- Un *job* del *scheduler* (DT-44) que lo genere después del turno: no le agrega latencia al lead, pero la
  ficha de traspaso saldría antes que el resumen, o habría que retrasarla. Descartada: el traspaso pasa
  una vez por conversación.
- Columna en `conversations`, como decía la historia: obliga a cambiar un contrato congelado por un dato
  que solo lee el panel. Descartada.
- Regenerar ante una violación, como el *pipeline* de DT-23: duplica el costo por un texto opcional.
  Descartada.

**Consecuencias.**
- El turno del traspaso tarda lo que tarde una llamada más al LLM (con Bedrock, un par de segundos).
- El aviso de traspaso del panel (`advisor_notifications`) y el primer mensaje de Telegram al asesor
  llevan el resumen al final, antes de la transcripción.
- Pendiente de verificar en vivo contra Bedrock con al menos tres conversaciones distintas.

## DT-47 — Las consultas de negocio viven en `db/metrics/`; el reporte semanal va a chats configurados

**Estado:** propuesta

**Contexto.** `N-10` manda cada semana, por Telegram, las cifras de `N-6` (colocadas, estancadas y demanda
sin cubrir) con un *job* del *scheduler* de A-17. El *scheduler* corre en `core`, pero las consultas de
`N-6` y `N-7` estaban en `services/panel/app/`, y cada servicio tiene su imagen: lo único que comparten
es `db/`. Además, `N-2` (DT-41) no definió un rol de gerente, así que no hay a quién mandarle el reporte.

**Decisión.**
- **Las consultas agregadas pasan a `db/metrics/`**: `inventory.py` (N-6) y `attribution.py` (N-7), con
  sus tests en `db/tests/`. Las dos imágenes ya copian `db/`. En el panel quedan
  `services/panel/app/inventory.py` y `attribution.py` como reexportaciones, así `N-8` sigue importando
  `app.inventory` y `app.attribution`. Las reglas de conteo tienen una sola fuente: el reporte y el panel
  muestran las mismas cifras.
- **Destinatarios por configuración:** `WEEKLY_REPORT_CHAT_IDS`, uno o más chats de Telegram separados
  por coma. Sin la variable no se programa nada. No hay tabla ni rol de gerencia, igual que en DT-41. El
  mensaje se manda con el bot de `core`.
- **Dos clases de *job* (migración `0013`):**
  - `weekly_report_plan`: uno solo pendiente a la vez, con la clave `semanal`. `core` lo programa al
    arrancar (es idempotente: un reinicio no lo duplica). Al ejecutarse, encola un `weekly_report` por
    destinatario y se vuelve a programar para la semana siguiente.
  - `weekly_report`: uno por semana y destinatario, con la clave `<año>-W<semana>:<chat>` y `once=True`
    (DT-44). Si Telegram falla o el bot no está arrancado, cuenta como fallo y se reintenta a 1 y 5
    minutos; una vez enviado, esa semana no se vuelve a mandar a ese chat.
- **Calendario:** el lunes a las 9:00, hora local (UTC-6, la de la agenda), con la última semana completa
  (lunes a lunes). El día y la hora se cambian con `WEEKLY_REPORT_WEEKDAY` y `WEEKLY_REPORT_HOUR`.
- **Contenido:** colocadas de la semana (total, monto y por zona), estancadas de más de 60 días (las 5 más
  antiguas, con "sin consultas" o "consultas sin visitas", y cuántas más hay) y las 3 búsquedas sin
  resultado más repetidas. Termina con el enlace a la vista de N-8 (`PANEL_PUBLIC_URL` +
  `/panel/<tu token>/negocio`), sin el token. Solo agregados, con la lista de DT-41: ningún lead.
  La historia sintética de N-9 se excluye salvo con `WEEKLY_REPORT_INCLUDE_SYNTHETIC=1`.

**Alternativas.**
- `core` le pide el reporte al panel por HTTP: agrega una dependencia de red entre servicios y un
  *endpoint* interno que hay que proteger. Descartada.
- Repetir en `core` las tres consultas que usa el reporte: más rápido, pero las reglas de conteo podrían
  divergir de las del panel. Descartada.
- Mandarlo a todos los asesores con Telegram (coherente con DT-41): cada asesor recibiría cada semana las
  cifras de toda la inmobiliaria sin haberlo pedido. Descartada; se puede sumar después con la misma
  variable.

**Consecuencias.**
- La vista de `N-8` vive en `/panel/<token>/negocio`, la ruta de `BUSINESS_PATH` en
  `services/core/app/weekly_report.py`. Si una de las dos cambia, hay que cambiar la otra.
- Un reporte que falla tres veces queda en `failed` y esa semana no se reenvía sola.
- Pendiente de verificar en vivo: poner un chat en `WEEKLY_REPORT_CHAT_IDS`, adelantar el `run_at` del
  plan pendiente y ver llegar el mensaje.

## DT-48 — Los ajustes del producto van en `config/<servicio>.env`; `.env` queda para secretos y la máquina

**Estado:** propuesta (#174).

**Contexto.** `.env.example` llegó a 36 variables de tres clases distintas: secretos (`POSTGRES_PASSWORD`,
`TELEGRAM_BOT_TOKEN`, `AWS_*`), valores que cambian según la máquina o el despliegue (puertos, proveedor y
modelos del LLM, URL del panel, chats del reporte) y ajustes del producto, que son reglas del sistema
iguales para todo el equipo (reintentos y límites del LLM, *scheduler*, alertas, horario del reporte,
umbral de atención). Cada ajuste estaba escrito en tres lugares (default del código, `${VAR:-default}` en
el compose y `.env.example`), y un test de infra exigía sumarlo a `.env.example`, así que cada historia lo
agrandaba.

**Decisión.**
- Los ajustes del producto viven en `config/core.env` y `config/panel.env`, versionados y comentados, y el
  compose los carga con `env_file` en su servicio. Cada servicio recibe solo los suyos.
- Cada valor se escribe `CLAVE=${CLAVE:-valor}`. Compose interpola los `env_file`, así que el valor sale
  del archivo salvo que la misma variable esté en `.env` o en la shell: un ajuste se puede cambiar en una
  máquina sin tocar el repo (`LLM_MAX_QUEUE_WAIT_SECONDS=60 docker compose up -d core`).
- `.env.example` documenta solo los secretos y lo que depende de la máquina o el despliegue.
  `WEEKLY_REPORT_CHAT_IDS` se queda ahí: no es secreto, pero son chats de personas reales.
- El código conserva sus defaults, iguales a los de `config/`, así que fuera de Docker se comporta igual.
- `tests/infra/test_compose.py` exige que cada variable viva en un solo lugar, que `config/` no tenga
  nada con forma de secreto y que cada valor tenga default y se pueda pisar.

**Alternativas.**
- **Un YAML con un *loader*** (PyYAML o pydantic-settings). Descartada por ahora: suma una dependencia,
  cambia los ~9 módulos que leen `os.environ` y los tests que usan `monkeypatch.setenv`, y los secretos
  seguirían en variables de entorno, con dos mecanismos a la vez. Conviene cuando la configuración tenga
  estructura (listas, anidamiento); hoy todo son escalares.
- **Dejar solo los defaults del código**, sin listarlos en ningún archivo. Descartada: cambiar un ajuste
  en Docker exigiría editar el compose, y nadie vería qué se puede ajustar.
- **Valores fijos en `config/`, sin `${CLAVE:-...}`.** Descartada: un `env_file` pisa lo que venga de
  `.env` o de la shell, y se perdían los overrides que ya usan `TESTING.md` y la verificación de B-12.

**Consecuencias.**
- Un ajuste nuevo se agrega en el default del código y en `config/<servicio>.env`, no en `.env.example`.
- El entorno que recibe cada contenedor no cambia: se comparó `docker compose config` antes y después.
- Cambiar un ajuste para todo el equipo es un cambio en `config/`, con su PR.

## DT-49 — La historia sintética de la demo no cambia el inventario y solo la ven las vistas agregadas

**Estado:** propuesta (N-9, #149).

**Contexto.** Sin historia, la vista de negocio (`N-8`) y el reporte semanal (`N-10`) salen en cero. `N-9`
siembra seis meses sintéticos sobre las 12 propiedades reales de `C-9`. Dos problemas:
- `set_status` parte del estado actual de la propiedad: no puede sembrar el alta como `disponible` de una
  propiedad que hoy está `apartado` o `cerrado` sin cambiarla primero.
- Los leads y eventos sintéticos viven en las mismas tablas que los reales, y varias consultas operativas
  no miraban `synthetic`: la cola y la ficha del asesor, el funnel, el reparto de leads entre asesores
  (cuenta su carga) y las alertas de propiedades nuevas (un "volvió a `disponible`" sembrado le habría
  escrito a un lead real).

**Decisión.**
- `db/property_history.record_past` escribe el historial pasado de una propiedad sin cambiar su estado:
  el último paso tiene que ser el estado que ya tiene. El seed sigue un guion fijo por propiedad que
  termina en el estado del catálogo, así que no cambia el inventario.
- Una propiedad con historial real, o en otro estado que el del guion, no se toca: la historia sembrada
  podría contradecirla. Si era la de un cierre, el cierre y su lead tampoco se siembran.
- Lo sintético solo existe para las vistas agregadas, y solo si se pide (`demo=1`,
  `WEEKLY_REPORT_INCLUDE_SYNTHETIC=1`). La cola, la ficha, "marcar en gestión", el funnel, el reparto de
  leads y las alertas lo excluyen.
- Determinista e idempotente: semilla fija, ids de leads y cierres con `uuid5`, y fechas contadas hacia
  atrás desde el día de la demo. Cada corrida borra lo sintético y lo vuelve a sembrar en una transacción.

**Alternativas.**
- **Propiedades sintéticas propias**, aparte del catálogo. Descartada: `properties` no tiene la marca, el
  catálogo del bot las ofrecería a leads reales, y la demo hablaría de propiedades que el bot no conoce.
- **Recorrer la historia con `set_status`**, forzando antes el estado inicial. Descartada: forzarlo sería
  cambiar `Property.status` fuera de `db/property_history.py`, o dejar un evento falso (`apartado` →
  `disponible`) como alta.
- **Una base aparte para la demo.** Descartada: el panel y el bot tendrían que apuntar a otra base, y la
  demo dejaría de mostrar la operación real junto a la historia.

**Consecuencias.**
- Una consulta nueva sobre `leads` o `property_status_events` que no sea una vista agregada tiene que
  excluir `synthetic`.
- La historia ocupa los días en que la demo corre: hay que volver a sembrarla el día de la demo para que
  "la última semana" tenga algo (`scripts/demo/up.sh` lo hace).
- `N-3` registrará cierres reales; una propiedad cerrada desde el panel tendrá historial real y el seed ya
  no la tocará.

## DT-50 — La foto de la propiedad viaja del turno al canal fuera del evento CT-1

**Estado:** propuesta (B-10, #136).

**Contexto.** B-10 pide que el bot mande la foto de la propiedad con el texto del turno como *caption*
cuando el turno trata de una sola propiedad. Hasta ahora el canal solo recibía el evento CT-1 de salida,
que tiene `additionalProperties: false` y no dice de qué propiedad habló el turno. La foto tiene que salir
de algún lado:
- `get_property` no devolvía `photo_url`, aunque la columna existe desde N-1.
- La foto la descarga Telegram desde una URL pública. El stack es local (DT-4) y no sirve archivos.

**Decisión.**
- `get_property` devuelve `photo_url` como campo opcional y nullable del contrato del catálogo.
  `search_properties` no la trae: la foto solo acompaña la ficha de una propiedad.
- `process_turn_reply` devuelve un `TurnReply`: el evento CT-1 de salida, la propiedad del turno y el
  estado de la FSM. `process_turn` y `POST /events` siguen devolviendo solo el evento: CT-1 no cambia.
- El turno trata de una sola propiedad cuando trae la del anuncio (*deep-link*) o cuando el agente llamó
  `get_property` sin buscar en el mismo turno. No cuenta si el código reemplazó la respuesta: agenda, alerta,
  traspaso a humano, frustración, o un turno que dejó la conversación en un estado terminal.
- **La foto sale una sola vez por propiedad (#210).** El agente vuelve a llamar a `get_property` cada vez
  que cita una cifra (A-5), así que la foto se repetía en cada respuesta sobre la misma propiedad. El
  turno guarda en `last_tool_results` (`photo_shown`) el id de la última propiedad cuya foto vio el lead,
  y no la vuelve a mandar mientras la respuesta siga en esa. Vuelve a salir al pasar a otra propiedad. No
  se usa la propiedad de interés recordada (`property_of_interest`): también la fija una búsqueda, y
  `search_properties` no trae foto.
- El canal manda la foto con `reply_photo` y, como *caption*, el mismo texto que habría mandado solo.
  Cae al texto sin foto si no hay foto, si el texto pasa el tope de 1024 unidades UTF-16 de un *caption*,
  o si Telegram rechaza la foto. Un `TimedOut` no cae al texto, por la misma razón que en #161: la foto
  pudo llegar y el lead recibiría la respuesta dos veces.
- Las fotos de la demo son URLs de Unsplash (uso libre). Son interiores, un terreno y un local por
  dentro; ninguna muestra fachada ni número de calle (A-6). El seed completa `photo_url` en las bases ya
  sembradas sin pisar una foto existente ni el resto de la fila.

**Alternativas.**
- **Agregar `photo_url` al evento CT-1 de salida.** Descartada: es un cambio de contrato (DT-8) que obliga
  a todos los consumidores y al arnés a convivir con un campo que solo usa Telegram.
- **Que el canal vuelva a consultar el catálogo.** Descartada: el canal tendría que adivinar de qué
  propiedad habló el turno, y sería una segunda llamada al catálogo por mensaje.
- **Fotos sintéticas dentro del repo, subidas como archivo.** Descartada: `photo_url` dejaría de ser una
  URL, y el contrato y el seed tendrían que distinguir rutas locales de URLs.

**Consecuencias.**
- Sin acceso a Unsplash el bot sigue respondiendo, solo con el texto. Para una demo sin internet, la foto
  se puede cambiar por otra URL editando `photo_url` en la base.
- Los tests de canal que espían el turno parchean `telegram.process_turn_reply`.
- B-11 usa el mismo `TurnReply` para decidir los botones.

## DT-51 — Un botón de Telegram es un mensaje del lead con texto fijo, y solo se ofrece donde funciona

**Estado:** propuesta (B-11, #137).

**Contexto.** B-11 pone botones "Agendar visita" y "Hablar con un asesor" bajo la respuesta cuando el turno
trata de una propiedad. Tocar un botón llega como `callback_query`, no como un mensaje de texto. Dos
riesgos:
- Un camino propio para el botón podría saltarse el guardrail de entrada, la FSM o el score, o pedir un
  campo nuevo en el evento CT-1.
- Pedir la visita solo arranca la reserva de B-8 en `RECOMMEND`. Después del *deep-link* el lead suele
  estar en `DISCOVERY` o `QUALIFY`: ahí el texto "Quiero agendar una visita" recibe una respuesta genérica
  y la FSM no se mueve.

**Decisión.**
- El canal traduce el botón al texto fijo "Quiero agendar una visita" o "Quiero hablar con un asesor", y
  ese texto hace un turno normal por `reply_turn`. El evento CT-1 no cambia.
- "Hablar con un asesor" sale siempre que el turno trata de una propiedad (la misma regla que la foto,
  DT-50). "Agendar visita" sale solo si la conversación quedó en `RECOMMEND`.
- Al tocarlo, el canal contesta el `callback_query` y quita el teclado de ese mensaje. Si Telegram no deja
  quitarlo, el turno se responde igual.
- La respuesta va al chat y no sobre el mensaje del botón, porque Telegram puede ya no entregar ese
  mensaje.
- Un segundo toque no duplica la reserva. En `SCHEDULE` el texto no trae horario y se vuelven a ofrecer.
  Ya agendada, la conversación terminó en `HANDOFF`. `book` es idempotente por `idempotency_key` (B-8).

**Alternativas.**
- **Mostrar los dos botones en cualquier estado**, como decía la historia. Descartada: "Agendar visita"
  antes de `RECOMMEND` respondería sin agendar, y en la demo parece un botón roto.
- **Que "Agendar visita" lleve la conversación a `SCHEDULE` desde cualquier estado.** Descartada: es
  cambiar la FSM de la sección 2 (track A) y saltarse la calificación que la precede.
- **Un tipo de evento nuevo para los botones.** Descartada: cambia CT-1 (DT-8) y abre un camino que podría
  no pasar por el guardrail de entrada.

**Consecuencias.**
- Con el agente *fake*, `RECOMMEND` se alcanza con búsquedas, no con una sola propiedad, así que
  "Agendar visita" casi no aparece. Con Bedrock aparece cuando el agente consulta la ficha en `RECOMMEND`.
- El bot tiene dos manejadores: uno para el texto y otro para los botones, filtrado por el
  `callback_data` `lead:visita` / `lead:asesor`.

## DT-52 — Cerrar una operación: quién puede, en qué orden se escribe y a qué hora queda

**Estado:** propuesta (N-3, #143).

**Contexto.** N-3 agrega "Cerrar operación" a la ficha del lead. La historia dice que solo cierra "el
asesor de la zona del lead". Desde DT-42, el panel también le muestra el lead al asesor que lo tiene
asignado, aunque sea de otra zona, y ese asesor ya puede "Marcar en gestión". Además, el formulario pide
una fecha y no una hora, y las métricas de N-6 cuentan los días en el mercado desde un `disponible`
anterior al cierre.

**Decisión.**
- Puede cerrar quien puede ver el lead: el asesor de su zona o el asignado (`_visible_to`, DT-42). Nunca
  un lead sintético (DT-49). Cualquier otro caso es el mismo 404 que el resto del panel (DT-5).
- **Orden de las escrituras.** Primero se valida el formulario y que la propiedad esté en el selector del
  lead. Después se cambia la propiedad a `cerrado` con `set_status`, que bloquea su fila. Al final van la
  fila en `deals` y el lead a `closed_won`. Si la propiedad ya estaba cerrada, `set_status` falla antes de
  escribir nada. La ruta hace `rollback` y muestra el error en el formulario (422).
- **Hora del cierre.** Si la fecha es hoy (hora local), se usa el momento del cierre. Si es un día pasado,
  las 12:00 de ese día en hora local. El evento de la propiedad y la operación llevan el mismo instante.
- **Selector.** Primero las propiedades que el lead tocó (`property_interest` no sintético), la más
  reciente arriba y en cualquier estado: las cerradas aparecen marcadas y deshabilitadas. Después las
  `disponible` o `apartado` de su zona. Solo se puede cerrar una propiedad de esa lista.
- El panel lee formularios, así que su imagen instala `python-multipart`.

**Alternativas.**
- **Solo el asesor de la zona**, como decía la historia. Descartada: el asignado ve la ficha y la gestiona
  (DT-42), pero recibiría 404 justo al cerrar su propio lead.
- **Pedir también la hora del cierre.** Descartada: es un campo más para el asesor y las métricas solo
  miran el día.
- **Medianoche para las fechas pasadas.** Descartada: un alta hecha ese mismo día quedaría después del
  cierre y la propiedad no contaría en los días en el mercado.

**Consecuencias.**
- Un lead puede tener más de una operación. La ficha las lista todas y el lead sigue en `closed_won`.
- Un cierre del panel cuenta en `/negocio` y en el reporte semanal sin cambios: esas consultas ya leen
  `deals` sin la marca sintética.
- Una propiedad cerrada desde el panel ya tiene historial real, y la historia sintética de N-9 no la toca
  (DT-49).

## DT-53 — El Markdown del agente se manda a Telegram como HTML

**Estado:** propuesta (#187).

**Contexto.** El agente arma sus respuestas en Markdown (`**negritas**`, viñetas con `-`) y el canal las
mandaba sin `parse_mode`: el lead veía los asteriscos literales. Los guardrails de salida revisan el texto
del agente, y el *caption* de la foto (B-10, DT-50) tiene un tope de 1024.

**Decisión.**
- **Renderizar, no quitar el formato.** `app/channels/formatting.py` convierte el Markdown al subconjunto
  que acepta `parse_mode="HTML"`: `**negritas**` a `<b>`, `*itálicas*` y `_itálicas_` a `<i>`, `` `código` ``
  a `<code>`, títulos `#` a negritas, viñetas `-`/`*`/`+` a `•` y los enlaces de Markdown a `texto (url)`. Todo lo
  demás se escapa, así que un `<`, `&`, `_` o `*` suelto no rompe el envío.
- **Mismo texto visible.** Solo cambian las marcas, así que el lead lee lo mismo que revisaron los
  guardrails. Lo que se guarda en `messages` sigue siendo el texto del agente, sin cambios.
- **Marcas cruzadas** (`**a *b** c*`), que darían HTML mal anidado, se mandan como texto plano escapado.
- **Si Telegram rechaza el HTML** (`BadRequest`), el mensaje se manda en texto plano, sin las marcas. La
  foto ya caía al texto ante cualquier error (DT-50), y el texto va primero en HTML y después en plano.
- **El tope del *caption*** se mide sobre el texto visible, que es como lo cuenta Telegram (después de
  parsear las entidades). Las `**` ya no sacan la foto de una respuesta que cabe.
- Solo cambia lo que se le manda al lead (`send_reply`). Las fichas del asesor y los mensajes del
  *scheduler* siguen en texto plano.

**Alternativas.**
- **Pedirle texto plano al agente y limpiar lo que se cuele.** Descartada: el prompt no garantiza nada, y
  la limpieza en el canal sería el mismo análisis del Markdown sin la ventaja del formato.
- **`parse_mode="MarkdownV2"`.** Descartada: exige escapar 18 caracteres (`.`, `-`, `(`, `!`…) que el
  agente usa en cualquier precio o frase, y el Markdown del agente no es MarkdownV2 (`**` no es negrita).

**Consecuencias.**
- La transcripción y el resumen de la ficha de traspaso al asesor (A-16) todavía pueden traer `**`. Si
  molesta, se usa el mismo `formatting.render` en `notifications.py`.
- Los *fakes* de Telegram de los tests aceptan `parse_mode`.

---

## DT-54 — El tipo de inmueble (`kind`) es un campo propio, separado de la operación (`type`)

**Estado:** aceptada (#188).

**Contexto.** `properties.type` guarda la operación (venta/renta/preventa). Qué es el inmueble
(departamento, casa, terreno, local, estudio) solo se podía adivinar por el `title`, así que
`search_properties` no podía filtrarlo: "busco depa en Zapopan" ofrecía casas y terrenos de la zona.

**Decisión.** Se agrega `properties.kind` (`String` NOT NULL con `CHECK` contra un vocabulario cerrado,
migración `0015`), `kind` es un filtro opcional más de `search_properties` y viaja en la salida de
`search_properties` y `get_property`. Vocabulario: `departamento | casa | terreno | local | estudio`.
El agente lo usa: el `inputSchema` de la tool lo describe, y el agente falso mapea
"depa"/"departamento" a `departamento` (la zona y el presupuesto siguen siendo del LLM real).

**Alternativas.** Derivar el tipo del `title` en cada búsqueda — descartado: frágil (un título cambia) y
no se puede filtrar en SQL. Un enum de Postgres — descartado: el esquema usa `String` + `CHECK` en todo
(DT-11), por consistencia.

**Consecuencias.** `kind` y `type` conviven y no son intercambiables. Queda pendiente (fuera de #188) que
`unmet_searches` (N-5), el ranking de N-6 y `property_alerts` (A-18) agrupen o filtren también por
`kind`: hoy una alerta nacida de una búsqueda con `kind` no lo guarda y podría avisar de otro inmueble.

---

## DT-55 — La experiencia del producto es en inglés, el inventario está en Colombia y se cobra en dólares

**Estado:** aceptada (#202). Reemplaza el país y la moneda de DT-34.

**Contexto.** Todo lo que ve una persona y todo lo que lee el LLM estaba en español: la conversación
con el lead, los prompts, los mensajes fijos, los guardrails que reconocen lo que escribe el lead, el
panel, los avisos al asesor y el reporte al gerente. El inventario de demo estaba en Guadalajara, con
precios en pesos mexicanos. Se decidió que el producto se presente en inglés, con propiedades en
Colombia y precios en dólares. Traducir también el código, los identificadores y la documentación
multiplicaría el trabajo sin cambiar nada de lo que se ve.

**Decisión.**

*Idioma.* Pasa a inglés todo lo que lee una persona del producto o el LLM:

- Los prompts (agente, clasificador de señales BANT-E y resumen del traspaso), las descripciones de las
  herramientas MCP y los textos que el catálogo devuelve en sus resultados (requisitos, mensajes de
  error, títulos y descripciones del inventario).
- Los mensajes fijos al lead: saludo, aviso de privacidad, respuestas de respaldo, oferta de asesor,
  botones de Telegram (B-11), alertas, recordatorios y seguimientos.
- Los guardrails y los reconocedores de texto del lead (`input_guard`, `promise_guard`,
  `address_guard`, las palabras de estado de `factuality`, la baja, el "avísame" de las alertas y la
  fecha de visita): reconocen inglés, y sus *gold sets* se traducen con los mismos umbrales (fair
  housing en diferencia 0).
- El panel, los avisos y la ficha de traspaso al asesor, y el reporte semanal al gerente.

Se quedan en español el código, los identificadores, los comentarios y *docstrings*, los mensajes de
log y de excepción (los lee el equipo, no un usuario del producto), `docs/` y las `description` de
`contracts/`. Hay dos excepciones, porque las lee el LLM: el *docstring* de cada tool de
`services/catalog/app/server.py` (es la descripción que publica MCP) y las `description` del
`inputSchema` de `contracts/mcp/` (`list_tools` publica ese esquema tal cual). Los mensajes de error y las
notas del catálogo también van en inglés: viajan como resultado de la tool.

*Solo se conversa en inglés.* Si el mensaje del lead está en español, el guardrail de entrada lo
detiene antes del LLM, la FSM y el score, y responde un texto fijo en inglés: solo se puede conversar
en inglés y que lo intente de nuevo. El turno queda en `audit_log` (`input.not_english`), no cuenta
como turno sin resolver (frustración), no suma al score y no avanza la conversación: como un mensaje
fuera de dominio, es un turno `otro`, así que solo mueve el saludo inicial (`GREETING` -> `DISCOVERY`). El
mensaje y el aviso quedan en el historial, y una oferta de horarios abierta se conserva. La detección es
determinista y local, sin LLM ni servicio externo (`services/core/app/language_guard.py`): cuenta las
palabras que solo son de un idioma, y el mensaje es español si tiene más de español que de inglés. "¿" y
"¡" cuentan como español; las tildes no, porque también las llevan los nombres propios. Las palabras de
los dos idiomas ("a", "no", "me", "solo") no cuentan, y ante el empate el mensaje pasa: es peor rechazar
a quien escribe en inglés. Antes de contar se quitan las ciudades y zonas del inventario y las vías con
número, y un artículo o preposición solo cuenta si la palabra siguiente va en minúscula ("La
Candelaria", "Santa Fe de Antioquia" pasan), para que "Do you have apartments in El Poblado?" pase.
Tiene su propio *gold set*: mensajes en inglés con nombres propios en español que deben pasar, y
mensajes cortos en español ("hola", "cuánto cuesta") que deben detenerse. Un saludo en español dentro de
un mensaje en inglés ("Hola! I'm looking for...") pasa. Tres cosas se procesan antes del filtro de idioma y siguen
reconociendo español:

- La **baja** ("dame de baja", "no me escribas"): retirar el consentimiento no puede depender del
  idioma.
- La **redacción de PII**: un dato personal no se guarda aunque el mensaje se rechace.
- Las **categorías protegidas** (fair housing): un mensaje en inglés con un gentilicio en español
  ("I'm colombiano") pasa el filtro de idioma y tiene que detectarse igual.

*Mercado.* Las propiedades están en Colombia: el inventario de demo, las zonas, las calles y el formato
de dirección son colombianos, y la hora local de la agenda y de las métricas pasa a la de Colombia
(UTC-5, sin horario de verano) en lugar de la de Guadalajara (DT-32). Los precios, los montos de
requisitos (enganche, ingreso mensual) y los montos de cierre son en dólares estadounidenses (USD). La
columna `price` sigue siendo un número: la moneda es una convención del producto, la dicen los prompts,
las descripciones de las herramientas y las plantillas, y no se guarda por fila. No hay que recalibrar el *scoring* que DT-34 anticipaba
para un cambio de país: el puntaje de presupuesto solo mira si el lead lo declaró (`score_budget`), y
los requisitos del catálogo son proporciones del precio (`down_payment_ratio`, `income_multiple`), que
no dependen de la moneda.

Los botones de Telegram (DT-51) también pasan a inglés, etiqueta y texto fijo: el texto es un mensaje
del lead y pasa por el filtro de idioma. El `callback_data` no cambia.

**Los valores de los contratos no cambian:** `type` (`venta/renta/preventa`), `kind`, `status` de
propiedad, los estados de la FSM, las intenciones y los valores de las señales (`declarado`,
`inmediata`...). Son vocabulario interno congelado por los *contract tests* y viven en la base. El
prompt le indica al LLM que nunca los muestre tal cual al lead, sino en inglés, y `factuality` reconoce
las formas en inglés ("available", "reserved", "sold"...) como el mismo estado canónico.
A una persona tampoco se le muestran: el panel y el reporte semanal los pasan por `db/labels.py`
("for rent", "reserved", "in progress"), y las razones del score dicen en inglés lo que significan
("Timing: within 3 months", no "urgency=3_meses"). Las rutas y los parámetros del panel (`/avisos`,
`/negocio`, `periodo=semana`) tampoco cambian: son direcciones, no texto.

**Alternativas.** Traducir también código y documentación — descartado: el costo se dispara y no cambia
nada visible. Un producto bilingüe que responda en el idioma del lead — descartado: duplica prompts,
mensajes y *gold sets*. Dejar que el LLM responda en inglés a un mensaje en español — descartado: el
lead no se entera de que debe escribir en inglés, y los guardrails en inglés no ven lo que escribió.
Detectar el idioma con una librería (`langdetect`, `lingua`) — descartado: con mensajes de chat de dos
o tres palabras y nombres propios en español fallan o pesan demasiado, y la regla se puede ajustar con
un *gold set*. Un campo de moneda por propiedad — descartado: todo el inventario está en una sola
moneda y agregarlo cambia el contrato sin necesidad.

**Consecuencias.** Los guardrails son la parte delicada: se traducen primero y con sus *gold sets*,
antes que los prompts. Todos conservan sus patrones en español, no solo la baja, la PII y fair housing: no
cuesta nada, los *gold sets* en español quedan como regresión, y cada uno suma su versión en inglés. En
Colombia la calle se nombra con un número, así que para `address_guard` una vía numerada sin placa
("Calle 10") es una referencia aproximada, y la dirección exacta es la que lleva la placa ("Calle 10 #
43-12"). La PII se adapta a Colombia (cédula de ciudadanía, NIT, teléfonos +57) y
`address_guard` a sus direcciones ("Calle 10 # 43-12", "Carrera 43A"). El inventario de demo se
reescribe, con precios en USD, y cambian los escenarios de `scripts/demo/conversar.py`, el arnés y las
ciudades de ejemplo de #194. Los tests que buscan texto en español se ajustan en el mismo PR que cambia
ese texto. #200 (gentilicios con errata) y #201 (enumerar requisitos) se resuelven ya en inglés. El
idioma por omisión del ruteo de asesores (`DEFAULT_LANGUAGE`, DT-42) pasa de `es` a `en`, y también el
`server_default` de `advisors.language` (migración `0016`, que pasa a `en` a los asesores que tenían el
valor por omisión): si no, el ruteo nunca encontraría un asesor de "zona e idioma". Esto no es un
*bugfix*: la semana de *hardening* (Z-1, #51) empieza cuando termine.

## DT-56 — `get_property` devuelve los requisitos de la operación

**Estado:** aceptada (#201, dentro de #202).

**Contexto.** Ante "¿qué requisitos piden?", el agente no los decía: preguntaba por el enganche del lead.
No podía hacer otra cosa, porque ninguna tool los devolvía: `check_requirements` solo dice si el lead
cumple, y para eso necesita sus montos. Además, sin el monto del enganche mínimo en un resultado, la
factualidad (A-5) habría marcado como inventado el número que citara. En la misma demo, el agente dio
por cumplido el enganche con el presupuesto del lead ("tienes $2.5 millones, el enganche te alcanza").

**Decisión.** La salida de `get_property` suma un campo opcional, `requirements`, con las reglas de
`data.REQUIREMENTS` que aplica `check_requirements`, ya calculadas sobre el precio: `down_payment_percent`
y `down_payment_min` (venta y preventa), `income_multiple` y `monthly_income_min` (renta) y
`references_required`. Lo que no aplica a la operación va en `null`. El prompt del agente le pide
enumerarlos antes de pedir el dato que falta y no decir que el lead cumple o no un requisito sin
`check_requirements`: el presupuesto no es el enganche. Las dos tools leen la misma regla
(`service.requirements_of`), así que el mínimo que se enumera es exactamente el que se verifica.

**Alternativas.** Una tool nueva (`get_requirements`) — descartado: el agente ya consulta la ficha de la
propiedad de la que se habla, y una tool más es una llamada más con el cupo de 10 por minuto. Llamar a
`check_requirements` con ceros para leer sus notas — descartado: deja un `meets_requirements=false` en
`last_tool_results`, del que A-9 deriva el score. Un guardrail de salida para "te alcanza" — descartado
por ahora: no es un número inventado sino una conclusión, y una regla literal sobre conclusiones daría
falsos positivos; queda en el prompt.

**Consecuencias.** El campo es opcional en `contracts/mcp/get_property.schema.json`, como `photo_url`:
una respuesta sin él sigue cumpliendo el contrato. Los montos van en USD (DT-55) y redondeados a
centavos.

## DT-57 — Una búsqueda sin resultados trae alternativas con un filtro menos, y las busca el código

**Estado:** aceptada (#213).

**Contexto.** En una prueba en vivo por Telegram con Bedrock, el lead pidió una casa en renta, que no
hay en el catálogo. El agente contestó que no había y volvió a pedirle la ciudad, aunque había
departamentos en renta en Laureles y casas en venta en Usaquen. Para nombrarlos necesita una búsqueda
que los traiga en ese turno, porque la factualidad (A-5) solo acepta lo que devolvieron las tools.
Primero se intentó con una regla en el prompt ("si no hay resultados, busca otra vez sin un filtro"),
pero en tres corridas el modelo nunca repitió la búsqueda.

**Decisión.**
- `app/alternatives.py` envuelve al catálogo, debajo de `SearchLog`. Si `search_properties` no trae
  ninguna propiedad, repite la búsqueda sin la zona y, si tampoco trae nada, sin el tipo de inmueble
  (`kind`), con la zona. Se queda con la primera que traiga algo.
- Lo que sale va en `alternatives`, con el filtro que se quitó en `alternatives_without`. `properties`
  sigue vacía.
- El prompt le dice al agente que, sin `properties` y con `alternatives`, diga que no hay exactamente
  lo que pidió y ofrezca las alternativas aclarando en qué no coinciden.
- El `AvailabilityGuard` (A-4) revalida las alternativas igual que `properties` y descarta las que ya
  no están disponibles. La factualidad cuenta el tamaño de `alternatives` como un número citable, como
  el de `properties`.

**Alternativas.**
- Dejarlo en el prompt: descartado, porque en vivo no se cumplía.
- Poner las alternativas en `properties`: descartado. Contarían para el ajuste de la necesidad (A-15),
  para las propiedades de interés y para la foto, como si fueran lo que el lead pidió. Además, la búsqueda
  dejaría de contar como demanda no atendida (N-5) y no se ofrecería la alerta (A-18).
- Resolverlo en el catálogo (un parámetro `fallback` en `search_properties`): descartado por ahora. Toca
  el contrato MCP y su *contract test*, y la regla de qué filtro se suelta es del agente, no del inventario.

**Consecuencias.**
- `SearchLog` ve solo la búsqueda del lead. La demanda no atendida registra lo que se pidió, y la alerta
  se ofrece con esos filtros.
- Una búsqueda vacía cuesta hasta dos llamadas más al catálogo, que no pasan por el LLM ni consumen el
  cupo de Bedrock.
- Una búsqueda con solo la zona ("¿qué tienen en Cali?") trae, sin la zona, todo el inventario como
  alternativas. Eso cubre en parte la pregunta de cobertura de #194.

## DT-58 — La cobertura sale de una tool del catálogo, `list_zones`

**Estado:** aceptada (#194).

**Contexto.** Ante "¿en qué zonas tienen propiedades?" o una ciudad sin cobertura, el agente no llamaba
ninguna tool. Con Haiku en la conversación terminaba en la respuesta de respaldo: la factualidad (A-5) no
encontraba las cantidades en ningún resultado. Con Sonnet contestaba bien las ciudades, que dice el
prompt, pero inventaba un barrio sin inventario ("Chicó"). La factualidad no lo detecta porque revisa
números y estados, no nombres. #194 pedía elegir entre una tool y una regla en el prompt para que el
agente busque sin filtros y resuma las zonas.

**Decisión.**
- El catálogo publica una sexta tool, `list_zones`, sin argumentos (contrato en
  `contracts/mcp/list_zones.schema.json`).
- Devuelve las zonas con propiedades `disponible`. Cada zona trae su ciudad, cuántas hay, sus
  operaciones (`types`) y sus tipos de inmueble (`kinds`). La ciudad sale de `address_approx` (DT-55).
- Cada zona lleva `status: "disponible"`, que dice qué se contó. Así "3 available properties in El
  Poblado" queda respaldado para la factualidad.
- El prompt le pide llamar a `list_zones` ante una pregunta de cobertura y nombrar solo las zonas que
  devuelve. Una ciudad que no aparece es una ciudad sin cobertura.

**Alternativas.**
- **`search_properties` sin filtros y una regla en el prompt:** descartado. Trae todas las fichas para
  contestar una lista de barrios, crece con el inventario y deja las cuentas a cargo del modelo.
- **Solo el prompt, con las zonas escritas:** descartado. Queda desactualizado en cuanto cambia el
  inventario.
- **Un guardrail de nombres de zona:** queda para después. Tiene que distinguir un barrio de cualquier
  nombre propio, y con la tool el modelo ya tiene la fuente.

**Consecuencias.**
- `list_zones` no pasa por el `AvailabilityGuard` (A-4). Cuenta el estado de la tabla, que es la misma
  fuente que `get_availability`.
- Core descubre las tools en tiempo de ejecución, así que no hubo que tocar el cliente.
- Los *contract tests*, el servidor MCP y el gold set de factualidad sobre el catálogo suman la tool y
  un turno `zonas`.

## DT-59 — La factualidad recuerda lo que las tools ya dijeron de cada propiedad, salvo el estado

**Estado:** aceptada (#217).

**Contexto.** La factualidad (A-5) revisaba cada número y cada estado de la respuesta contra las tools del
turno en curso. Pero el modelo ve toda la conversación. Si dos turnos antes dio bien el precio de una
propiedad y ahora lo repite sin volver a llamar la tool, la respuesta se bloqueaba. Después se regeneraba,
y si el modelo no volvía a buscar, el lead recibía la respuesta degradada. En la línea base de la
evaluación en vivo (#216) era el patrón de la mayoría de las regeneraciones: 24% de los turnos.

**Decisión.**
- **Qué se guarda.** De cada propiedad que devuelve una tool (la ficha de `get_property`, los resultados de
  `search_properties` y sus alternativas) se guardan los campos que se pueden citar: precio, m²,
  recámaras, baños, amenidades, requisitos, zona, título y dirección aproximada. También la hora en que se
  vio.
- **Dónde.** En `last_tool_results` (`verified_facts`), que ya viaja de turno en turno (B-8). No hace falta
  migración.
- **Cómo se usa.** La factualidad acepta un número si está en las tools del turno, en lo que escribió el
  lead o en esa memoria. El guardrail de promesas (A-7) acepta las amenidades recordadas.
- **El estado nunca se toma de la memoria.** Ni siquiera se guarda: una propiedad disponible hace una hora
  puede estar apartada ahora. Se sigue exigiendo de las tools del turno, que el `AvailabilityGuard`
  revalida (A-4).
- **Tampoco se guardan** la dirección exacta (A-6) ni la foto: una URL trae dígitos que no son de la
  propiedad.
- **Vencimiento y tope.** Un hecho vale 24 horas desde la última vez que una tool lo devolvió. Se guardan
  como mucho las 20 propiedades vistas más recientemente.

**Alternativas.**
- **Exigir siempre la tool del turno y endurecer el prompt de corrección:** es lo que había. El modelo no
  siempre vuelve a buscar, y cada regeneración cuesta una llamada más con una cuota de 10 por minuto.
- **Revisar contra el texto de las respuestas anteriores del agente:** descartado. Esas respuestas ya
  pasaron la factualidad, pero también llevan lo que dijo el lead y los textos fijos. Lo recordado tiene
  que salir de una tool, no de lo que el modelo escribió.
- **Recordar también el estado:** descartado. Es justo lo que cambia entre turnos (A-4).

**Consecuencias.**
- Un precio que cambió en el catálogo dentro de las 24 horas se aceptaría hasta que una tool lo vuelva a
  devolver. Lo acota el vencimiento, y la ficha de la propiedad lo corrige en cuanto se consulta.
- `tool_results` guarda el último resultado de cada tool del turno: si el modelo llama dos veces a
  `get_property` en el mismo turno, solo se recuerda la segunda.
- La evaluación en vivo (#216) mide el efecto con la tasa de regeneraciones, contra la línea base.

## DT-60 — Los datos de la propiedad los escribe el código: el modelo pone marcadores

**Estado:** aceptada (#243, con #219 y #218).

**Contexto.** La factualidad (A-5, DT-23) lee la prosa del modelo para adivinar si cada número y cada estado
salió de una tool. Es una comparación literal, y cada redacción nueva que apareció en vivo trajo su
excepción:

- un "available" que hablaba del dinero del lead o de otras propiedades (#202, #220);
- el número del lead confirmado como precio (#230);
- un número que el modelo no reconocía en otro formato (#213).

Hay además dos fallas que la comparación literal no puede ver:

- **El dato real de otra propiedad.** Si el modelo le pone a una propiedad el precio de otra, el número
  existe en las tools y pasa (punto 5 de la revisión, #218).
- **Los valores internos en español** (`apartado`, `departamento`): el prompt le pide al modelo que los
  traduzca, y nada lo comprueba (#219).

**Decisión.**

- **Marcadores.** El modelo no escribe los datos de una propiedad, escribe un marcador con su referencia y
  el campo: "It's `{prop_00231.price}`, with `{prop_00231.bedrooms}` bedrooms". El código lo completa antes
  de mandar la respuesta, y el lead lee "It's $185,000, with 2 bedrooms".
- **Campos de una propiedad:**
  - `price`, `m2`, `bedrooms`, `bathrooms`;
  - `title`, `zone`, `address_approx`, `amenities`;
  - `type` y `kind`, que se escriben en inglés (for sale, apartment…);
  - `status`, también en inglés (available, reserved, off the market…);
  - `requirements.down_payment_percent`, `requirements.down_payment_min` y `requirements.monthly_income_min`;
  - `address_exact`, que solo se completa con `lead_qualified` (A-6). Sin calificar, el catálogo la devuelve
    `null` y el marcador no se puede completar.
- **Otros marcadores:**
  - `{search.count}`: cuántas propiedades trajo la búsqueda del turno;
  - `{zones}`: la cobertura de `list_zones`, armada por el código ("El Poblado and Laureles in Medellin…");
  - de una promoción, `{promo_<id>.title}`, `{promo_<id>.description}` y `{promo_<id>.valid_until}`.
- **De dónde sale cada valor.** De las tools del turno y de la memoria de hechos (DT-59). El estado sale
  solo de las tools del turno, igual que hoy. Si el texto pide el estado de una propiedad conocida que ninguna
  tool del turno dio (`{ref.status}`, o "it's reserved" sin marcador), lo consulta el código con
  `get_availability`: es una llamada al catálogo, no al LLM, y como mucho tres por respuesta.
- **Formato.**
  - El dinero va con `$` y separador de miles, y un `$` que el modelo haya escrito antes del marcador no se
    duplica. Lo mismo con el `%` del porcentaje.
  - Las listas van separadas por comas, con "and" antes del último elemento.
  - Lo arma un renderer chico, con tests.
- **Qué se bloquea** (mismo pipeline de regenerar o degradar, DT-23):
  - **un marcador que no se puede completar:** una propiedad o un campo que ninguna tool dio, o un valor
    `null`;
  - **un número escrito fuera de un marcador,** salvo:
    - los que dijo el lead en la conversación;
    - la numeración de una lista al principio de la línea ("1.", "2)");
    - un dato de la propiedad de la que habla. Cuál es lo dice la línea: la que nombra con un marcador o con
      su referencia ("• Studio in El Poblado, 35 m² (ref. prop_00240)"). Si nombra una propiedad y el número
      es de otra, se bloquea. Si no nombra ninguna, tiene que ser de una sola, con prioridad para las del turno:
      si dos lo tienen, podría ser el de la otra;
  - **una referencia `prop_…` fuera de un marcador** que ninguna tool dio.
- **La corrección le dice al modelo qué marcador usar.** Si un número suelto es un dato de varias propiedades,
  `_correction_prompt` nombra los marcadores entre los que elegir ("write `{prop_00231.bathrooms}` or
  `{prop_00232.bathrooms}` instead"). Así, la regeneración no depende de que el modelo lo adivine.
- **Qué marcó el guardrail queda en el log del core,** en cada intento: el tipo y el marcador sugerido, y con
  `TRACE_CONTENT=1` también el valor y el borrador (OBS-7). Así se ajusta el prompt con la evaluación en vivo.
- **Lo que se sigue revisando como hoy:**
  - **los estados escritos fuera de un marcador,** con las excepciones de #202 y #220;
  - **la regla de #230,** sobre los números del lead que se afirman como dato de la propiedad: "Is it
    $150,000?" → "Yes, it's $150,000" no usa ningún marcador;
  - **la dirección exacta en prosa (A-6)** y **las promesas (A-7),** sobre el texto ya completado.
- **El historial.** El modelo ve en el historial el texto completado, el mismo que leyó el lead. Necesita los
  valores para razonar ("is it within my budget?"), y no hace falta guardar dos versiones de cada
  mensaje.
- **El prompt** (#219). Las reglas de "mention a price only if you got it from a tool in this same reply"
  y de cómo traducir los valores internos se reemplazan por la regla de los marcadores. Cuando no hay
  `address_exact`, el prompt da el motivo real: la dirección exacta se comparte cuando el lead está calificado
  (B-7: presupuesto, decisión y plazo), y hasta entonces va la aproximada. Así, el modelo deja de inventar
  uno.
- **Decisiones del flujo con salida estructurada.** El clasificador (Haiku) ya devuelve JSON, y se le suman
  tres campos booleanos:
  - `asks_for_person`: el mensaje pide hablar con una persona, o acepta la oferta de un asesor que le
    acaban de hacer. Reemplaza a `handoff.asks_for_a_person` (#228);
  - `wants_alert`: el lead pide que le avisen cuando entre algo así. Reemplaza a `alerts.is_request`;
  - `wants_visit`: el lead quiere visitar una propiedad. Lo usa #229.

  Si el campo falta, o no es booleano, se usa la regla de hoy como respaldo. `FakeAgent` no tiene
  clasificador y sigue con las reglas.
- **Llamadas.** La cantidad de llamadas por turno no cambia: el renderer es código, y los campos nuevos van en
  la misma llamada al clasificador.

**Alternativas.**
- **Seguir con la comparación literal y sumar excepciones:** es lo que había. Cada redacción nueva necesita
  una regla, y las reglas chocan entre sí (#234).
- **Un segundo LLM que juzgue cada respuesta:** descartado. Suma una llamada por turno contra una cuota de 10
  por minuto que no se puede subir (DT-19), y su criterio tampoco es determinista.
- **Bloquear todo número escrito fuera de un marcador:** fue la primera versión. En dos corridas cortas de la
  evaluación en vivo (#243) el modelo siguió escribiendo a mano los datos de la propiedad de la que hablaba,
  que tiene a la vista en el historial: se regeneraron 24% y 43% de los turnos, contra el 11% de la línea base
  de DT-59, y cada regeneración es una llamada más. Se aceptan los de la propiedad que nombra la línea, o los de
  una sola propiedad: es el paso atrás que esta misma decisión preveía.
- **Aceptar fuera de un marcador cualquier número que exista en las tools,** como hacía la factualidad:
  descartado. Es lo que deja pasar el dato de otra propiedad cuando dos lo tienen.
- **Guardar en el historial la versión con marcadores:** descartado. El modelo dejaría de ver los valores con
  los que razona, y hace falta una columna nueva en `messages`.
- **Detectar en prosa los barrios que no están en el catálogo** (#218): sin una lista de todos los barrios
  posibles no se puede hacer de forma determinista. Se cubre lo que sí se puede: las zonas de una propiedad y
  la cobertura salen de marcadores (`{prop_….zone}`, `{zones}`), y las referencias tienen que existir.

**Consecuencias.**
- Un precio inventado se sigue bloqueando, y con un marcador no se puede escribir.
- **Límite conocido:** escrito a mano en una línea que no nombra ninguna propiedad, el valor de una pasa aunque
  la frase hable de otra, si ninguna otra tiene ese valor ("The 2-bedroom one costs $129,000", cuando $129,000 es la de un dormitorio). Con el
  marcador no pasa. El gold set lo deja fijado como límite.
- Un número escrito en palabras ("two bedrooms") no pasa por la regla de los números, igual que hoy.
- `factuality.py` queda para los estados y para la regla de #230, y los gold sets de DT-23 siguen igual. Los
  marcadores tienen su propio gold set, con salidas reales del catálogo.
- `address_guard` sigue igual: un número de calle escrito a mano puede coincidir con un dato de una propiedad.
- #218 se cierra con lo que cubre esta decisión. Un barrio inventado que el modelo nombre en prosa sigue sin
  bloquearse, y lo mide la evaluación en vivo (`INVENTED_ZONES`).
- La evaluación en vivo (#216) mide el efecto en las regeneraciones, las respuestas degradadas y la tool
  esperada. Mientras se itera, con una repetición de los escenarios 01, 03, 04 y 07.

## DT-61 — La visita se pide desde cualquier estado, y el embudo avanza por señales

**Estado:** aceptada (#229).

**Contexto.** La agenda de visitas (B-8, DT-32) y el botón "Schedule a visit" (B-11) solo existían en
`RECOMMEND`. Para llegar ahí, la FSM pedía dos etiquetas exactas del clasificador: `busqueda_propiedad` en
`DISCOVERY` y `consulta_precio` en `QUALIFY`. `fsm.py` lo dejaba como una política mínima a reemplazar.

En la evaluación en vivo de #217, `06_requisitos_y_fair_housing` terminó las 5 veces en `DISCOVERY`, con
presupuesto, requisitos y `check_requirements` pasados. Un lead de un anuncio pregunta por la disponibilidad,
no busca: ningún mensaje se clasificaba `busqueda_propiedad`. Si pedía la visita, el pedido no llegaba a la
agenda y lo contestaba el LLM, que no tiene tool de agenda.

**Decisión.**

- **La visita se pide desde cualquier estado activo** (`VISIT_STATES`: todos menos `SCHEDULE` y los
  terminales). La pide la intención `agendar_visita` o el campo `wants_visit` del clasificador (DT-60).
  - Si hay una propiedad de interés (la del turno o la recordada), el código ofrece los horarios y la FSM pasa
    a `SCHEDULE`.
  - Si no la hay, antes de `RECOMMEND` contesta el agente, que pregunta cuál. En `RECOMMEND` sigue como
    antes: se pide el horario preferido.
- **A `SCHEDULE` se llega cuando el código arrancó la agenda** (`visit_started`), no por una intención. Así la
  FSM y la agenda no pueden quedar desfasadas.
- **El embudo avanza por señales:**
  - buscar o mirar una propiedad (`search_properties`, `get_property` o el anuncio) cierra `DISCOVERY`;
  - el primer dato de calificación en la señal acumulada (presupuesto, cómo se decide, cuándo, o el resultado
    de `check_requirements`) cierra `QUALIFY`.

  La intención sigue valiendo: es lo único que da el fake LLM (CT-7).
- **El botón "Schedule a visit"** aparece bajo la respuesta sobre una propiedad en cualquier estado de
  `VISIT_STATES`.

**Alternativas descartadas.**

- **Saltar el embudo directo a `RECOMMEND` con el puntaje:** descartado. Los estados intermedios son los que
  mide el embudo (`funnel_events`), y un salto los dejaría vacíos.
- **Dejar que el LLM agende con una tool:** descartado por DT-32. La agenda la maneja el código.

**Consecuencias.**

- Un turno avanza un solo estado: un lead que en un mensaje busca y da su presupuesto pasa a `QUALIFY`, y a
  `RECOMMEND` en el turno siguiente.
- Los tests de racha y de embudo usan un evento sin el anuncio: en Telegram solo lo trae el `/start`.
