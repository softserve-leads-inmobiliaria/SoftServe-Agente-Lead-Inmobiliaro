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
