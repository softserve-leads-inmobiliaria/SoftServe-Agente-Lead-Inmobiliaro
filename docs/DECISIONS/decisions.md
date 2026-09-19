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
