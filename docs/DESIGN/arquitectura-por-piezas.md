# Arquitectura por piezas — 9 de octubre de 2026

Material de apoyo para las preguntas de arquitectura de la presentación del
[estado del 9 de octubre](../archive/presentations/2026-10-09-estado-del-proyecto.md). Hay un diagrama por pieza del sistema, con lo
mínimo para explicarla y las preguntas que suelen salir. Todo describe `main` al 9 de octubre (`06668ae`). El
porqué de cada elección está en [`decisions.md`](../DECISIONS/decisions.md), las tablas compartidas en
[`design.md`](design.md) y el detalle de los guardrails en [`guardrails.md`](guardrails.md).

| # | Pieza | Para preguntas sobre… |
| ---: | :--- | :--- |
| 1 | [Mapa general](#1-mapa-general) | Qué servicios hay y cómo se hablan |
| 2 | [Canal de Telegram](#2-canal-de-telegram) | Deep-link, orden de los mensajes, foto y botones |
| 3 | [El turno completo](#3-el-turno-completo) | Qué pasa entre que llega un mensaje y sale la respuesta |
| 4 | [Guardrail de entrada](#4-guardrail-de-entrada) | `/stop`, PII, fair housing, *jailbreak*, idioma |
| 5 | [Clasificador](#5-clasificador) | Qué decide Haiku y por qué no hay más llamadas |
| 6 | [Agente con tools](#6-agente-con-tools) | Cómo usa el catálogo Sonnet y cuándo no se le deja contestar |
| 7 | [Guardrails de salida](#7-guardrails-de-salida) | Factualidad, dirección, promesas, regenerar y degradar |
| 8 | [Marcadores](#8-marcadores-el-código-escribe-los-datos) | "¿Y si inventa un precio?" |
| 9 | [FSM](#9-fsm-el-embudo) | Estados del embudo y cómo se avanza |
| 10 | [Score BANT-E](#10-score-bant-e) | Cómo se califica y fair housing |
| 11 | [Agenda de visitas](#11-agenda-de-visitas) | Cómo se reserva sin que el LLM toque la agenda |
| 12 | [Traspaso a humano](#12-traspaso-a-humano) | Cuándo pasa al asesor y qué recibe |
| 13 | [Catálogo MCP](#13-catálogo-mcp) | Las tools, por qué MCP y la disponibilidad |
| 14 | [Scheduler](#14-scheduler-tareas-diferidas) | Seguimientos, recordatorios, alertas, reporte semanal |
| 15 | [Panel y landing](#15-panel-y-landing) | Qué ve el asesor, el gerente y el público |
| 16 | [Datos](#16-datos) | Tablas y qué guarda cada una |
| 17 | [Llamadas al LLM: cuota y reintentos](#17-llamadas-al-llm-cuota-y-reintentos) | 10 RPM, colas, respaldo |
| 18 | [Observabilidad](#18-observabilidad) | Trazas, auditoría y funnel |
| 19 | [Cómo se prueba](#19-cómo-se-prueba) | Qué corre en CI y qué contra Bedrock |

---

## 1. Mapa general

```mermaid
flowchart LR
    subgraph Usuarios
        LEAD([Lead])
        ASESOR([Asesor])
        GER([Gerente])
    end

    subgraph Stack["docker compose · todo local"]
        CORE["core<br/>canal · turno · FSM · score<br/>guardrails · agente · scheduler"]
        CAT["catalog<br/>MCP /mcp + agenda /scheduling"]
        PANEL["panel<br/>cola · ficha · funnel<br/>negocio · landing"]
        PG[("Postgres<br/>+ pgvector")]
        PHX["Phoenix<br/>trazas"]
    end

    BR["Amazon Bedrock<br/>Sonnet 4.5 · Haiku 4.5"]

    LEAD <-->|"Telegram (long polling)"| CORE
    LEAD -->|"navegador: /properties"| PANEL
    CORE -->|"MCP streamable HTTP"| CAT
    CORE -->|"HTTP: agenda"| CAT
    CORE <-->|"Converse API"| BR
    CORE --> PG
    CAT --> PG
    PANEL --> PG
    CORE -.->|OTLP| PHX
    CORE -.->|"avisos opcionales"| ASESOR
    ASESOR -->|navegador| PANEL
    CORE -->|"reporte semanal"| GER
```

- **Tres servicios propios** en contenedores separados, más Postgres y Phoenix. Todo corre en local con un
  `docker compose` (`bash scripts/demo/up.sh`). La única dependencia externa es Bedrock.
- **`core` es el único que habla con el lead y con el LLM.** `catalog` no sabe nada de conversaciones;
  `panel` solo lee y escribe Postgres.
- **Telegram por *long polling*** (DT-4): no hace falta URL pública ni *webhook*.

**Si preguntan:**

- *¿Por qué no un monolito?* El catálogo se expone como servidor MCP: así el LLM ve las tools con su
  contrato, y el catálogo se prueba solo contra ese contrato (`contracts/mcp/`).
- *¿Y WhatsApp?* El canal traduce a un evento canónico (CT-1) antes del turno. WhatsApp sería otro adaptador,
  sin tocar el agente (DT-20).

## 2. Canal de Telegram

```mermaid
sequenceDiagram
    participant L as Lead
    participant TG as Telegram
    participant CH as core · canal
    participant T as core · turno
    L->>TG: mensaje, botón o /start prop_00231
    TG->>CH: update (long polling)
    CH->>CH: lock del chat: uno a la vez, en orden (#227)
    CH->>CH: identidad: lead y conversación por telegram_user_id
    CH->>CH: deep-link: prop_… o camp_… → evento canónico CT-1
    CH->>T: process_turn_reply(evento)
    T-->>CH: respuesta + propiedad destacada + estado
    CH->>CH: Markdown → HTML · aviso de privacidad en el 1er turno
    CH->>TG: foto con caption, o texto · botones
    TG->>L: respuesta
    CH->>T: after_reply: resumen y avisos al asesor (#231)
```

- **Un lock por chat** (#227): si el lead manda tres mensajes seguidos, se atienden de a uno y en orden.
- **El deep-link** `?start=prop_00231` llega como dato del evento: el agente ya sabe de qué propiedad habla
  (B-3). `camp_…` guarda la campaña de origen (N-5).
- **La foto** sale cuando el turno trata de una sola propiedad, con el texto como *caption* (B-10).
- **Los botones** ("Talk to an agent", "Schedule a visit") equivalen a escribir su texto fijo: pasan por el
  mismo turno, guardrails y score (B-11).
- **Lo lento va después de responder** (#231): el resumen del traspaso y los avisos al asesor no hacen
  esperar al lead.
- **Reintentos de envío** (#161): hasta 3, respetando `RetryAfter`; no reintenta un `TimedOut` para no
  duplicar.

## 3. El turno completo

```mermaid
flowchart TD
    E["Evento CT-1"] --> GI["Guardrail de entrada"]
    GI -->|"/stop"| STOP["Baja: cancela jobs y alerta<br/>sin LLM"]
    GI -->|"español · fuera de dominio · jailbreak"| FIX["Texto fijo<br/>sin LLM, no cuenta como frustración"]
    GI -->|normal| CTX["Contexto: historial, hechos<br/>verificados, propiedad del anuncio"]
    CTX --> AG["Agente<br/>clasificador + Sonnet con tools<br/>+ guardrails de salida"]
    AG --> VIS{"¿Pide visita<br/>y hay propiedad?"}
    VIS -->|sí| AGENDA["Agenda por código<br/>reemplaza la respuesta"]
    VIS -->|no| ALR{"¿Pide alerta?"}
    ALR -->|sí| ALERT["Crea la alerta"]
    ALR -->|no| ADV
    AGENDA --> ADV
    ALERT --> ADV
    FIX --> ADV
    ADV["advance: una transacción<br/>FSM · score · mensajes · auditoría<br/>interés · visita · jobs"]
    ADV --> OUT["Respuesta al canal"]
    OUT --> AFTER["Después: tarjeta de calificado,<br/>resumen y ficha de traspaso"]
```

- **Todo el estado del turno se guarda en una sola transacción** (`turn.advance`, DT-14): la FSM, el
  score, los mensajes, la auditoría, el interés por propiedad, la visita y los *jobs*. Nunca queda el
  historial desfasado del estado.
- **El estado se bloquea por conversación** (`StateStore.lock`), además del lock del canal.
- **Los textos fijos no gastan LLM** y no suman a la racha de frustración.
- **Cada turno deja su decisión** en `audit_log` (`turn.decision`): intención, flags, propiedad y ruta
  (`agent`, `agenda`, `protected`, `handoff`, `degraded`, `llm_error`…).

## 4. Guardrail de entrada

```mermaid
flowchart LR
    M["Mensaje del lead"] --> O{"/stop o<br/>'unsubscribe'?"}
    O -->|sí| BAJA["consent = withdrawn<br/>cancela jobs y alerta"]
    O -->|no| PII["PII no pedida<br/>cédula, pasaporte, tarjeta"]
    PII --> FH["Categoría protegida<br/>nacionalidad, religión, familia"]
    FH --> LANG{"¿Español?"}
    LANG -->|sí| EN["'Please write in English'<br/>no llega al LLM"]
    LANG -->|no| JB{"¿Jailbreak o<br/>fuera de dominio?"}
    JB -->|sí| SAFE["Respuesta acotada<br/>sin reflejar la instrucción"]
    JB -->|no| LLM["Al agente: el texto ya filtrado"]
```

- **Determinista, sin LLM** (`input_guard.py`, `language_guard.py`, DT-15, DT-55).
- **La PII** no se guarda y se le recuerda al lead qué datos hacen falta.
- **Fair housing en la entrada:** la mención se quita o se generaliza ("my wife" → "my partner", "my kids"
  → "my family") antes de que la vea el LLM. Así el lead no pierde los puntos de Authority (DT-38, DT-40).
- **"I'm Colombian, does that matter?"**: si el clasificador marca `asks_if_it_matters`, sale una respuesta
  fija: no influye (DT-69).
- **Idioma:** cuenta palabras exclusivas de cada idioma y no cuenta nombres de zonas ("El Poblado") ni
  direcciones ("Calle 10"). La baja, la PII y fair housing se aplican antes, en los dos idiomas.
- **Regla general:** nunca corta la conversación en seco salvo `/stop`.

## 5. Clasificador

```mermaid
flowchart LR
    IN["Mensaje filtrado<br/>+ última respuesta<br/>+ propiedades mostradas"] --> H["Haiku 4.5<br/>JSON · temperature 0"]
    H --> SIG["Señales del score<br/>presupuesto · decisión · urgencia"]
    H --> INT["intent"]
    H --> REF["refers_to<br/>¿de cuál propiedad habla?"]
    H --> WHEN["visit_when<br/>¿qué día y franja?"]
    H --> FL["7 flags<br/>wants_visit · wants_alert · asks_for_person<br/>frustrated · asks_unlisted<br/>needs_lookup · asks_if_it_matters"]
    SIG --> SC["Score en Python"]
    INT --> FSM["FSM y rutas del turno"]
    REF --> AGENDA["Agenda y memoria"]
    WHEN --> AGENDA
    FL --> RUTAS["Traspaso · alerta · visita<br/>nota 'lo confirma un asesor'<br/>tool obligatoria · respuesta fija"]
```

- **Una sola llamada a Haiku por turno, que ya existía** para las señales del score. Desde #247 devuelve
  además lo que antes se adivinaba con expresiones regulares sobre el texto (DT-62).
- **`refers_to` elige entre las propiedades ya mostradas**; si no se sabe cuál, el agente pregunta y no se
  adivina (DT-63).
- **`asks_unlisted`**: parqueo, mascotas, piso… no están en la ficha. Se contesta que lo confirma un asesor,
  nunca con sí o no (DT-66).
- **`needs_lookup`** obliga a Sonnet a llamar una tool antes de escribir (DT-68).
- **`frustrated`**: la racha de 3 turnos sin resolver solo traspasa si el último mensaje muestra molestia
  (DT-65).
- **Se mide con un gold set** de mensajes reales (`harness/gold/classifier.jsonl`, hoy 93) contra Haiku
  real: un cambio de prompt se acepta solo si ningún campo baja. No corre en CI porque necesita Bedrock.

**Si preguntan:** *¿por qué no un segundo LLM que juzgue las respuestas?* Con 10 llamadas por minuto, sería
una llamada más por turno. Preferimos que el clasificador devuelva campos y que el código verifique.

## 6. Agente con tools

```mermaid
flowchart TD
    C["Clasificación"] --> HUM{"¿Pide una persona<br/>y el mensaje lo confirma?"}
    HUM -->|sí| HT["Texto fijo de traspaso"]
    HUM -->|no| VR{"¿La visita la<br/>maneja el código?"}
    VR -->|sí| AGD["Respuesta vacía:<br/>la escribe la agenda"]
    VR -->|no| PR{"¿Pregunta si una<br/>categoría protegida importa?"}
    PR -->|sí| PA["Respuesta fija<br/>de fair housing"]
    PR -->|no| CHAT["Sonnet 4.5 con las 6 tools MCP<br/>hasta 3 rondas de tools<br/>needs_lookup → toolChoice: any"]
    CHAT --> OG["Guardrails de salida"]
    OG -->|pasa| OK["Respuesta"]
    OG -->|no pasa| RG["Regenera 1 vez<br/>con la instrucción de qué falló<br/>y las tools disponibles"]
    RG --> OG2["Guardrails de salida"]
    OG2 -->|pasa| OK
    OG2 -->|no pasa| DG["Degrada: texto seguro<br/>que ofrece un asesor"]
```

- **El modelo solo escribe cuando hace falta redactar.** El traspaso, la respuesta de fair housing y la
  agenda son textos del código.
- **`lead_qualified` no lo controla el LLM** (`CODE_CONTROLLED_ARGUMENTS`): el código lo agrega a la llamada
  de la tool, y el catálogo solo da la dirección exacta con ese valor.
- **Un traspaso a humano es terminal:** si el clasificador dice "quiere una persona" pero el mensaje no lo
  pide, el turno sigue como uno más (#228).
- **La regeneración reutiliza los resultados de las tools** del primer intento (#232).
- **Una respuesta cortada por `max_tokens`** termina en su última frase completa (#235).

## 7. Guardrails de salida

```mermaid
flowchart LR
    D["Borrador del modelo<br/>con marcadores"] --> MK["Completar marcadores<br/>con tools del turno<br/>y hechos verificados"]
    MK --> ST["Estados que faltan:<br/>el código consulta<br/>get_availability · máx. 3"]
    ST --> CHK["Revisiones"]
    CHK --> A5["A-5 factualidad<br/>marcador sin dato · número suelto<br/>referencia inventada · estado"]
    CHK --> L230["#230 número del lead<br/>no confirma un dato"]
    CHK --> A6["A-6 dirección exacta<br/>solo si lead_qualified"]
    CHK --> A7["A-7 promesas<br/>crédito, mascotas, precio,<br/>apartar, garantía, amenidades"]
    CHK --> P234["#234 'te busco después'<br/>sin consultar"]
    A5 --> V{"¿Alguna violación?"}
    L230 --> V
    A6 --> V
    A7 --> V
    P234 --> V
    V -->|no| SEND["Respuesta final"]
    V -->|sí| REGEN["Regenerar → degradar"]
```

- **Todas comparten el mismo *pipeline* de regenerar y degradar** (DT-23).
- **La dirección y las promesas se revisan sobre el texto ya completado**, tal como lo va a leer el lead.
- **La memoria de hechos** (`facts.py`, DT-59) guarda durante 24 horas los campos citables de cada propiedad
  que una tool dio en la conversación, nunca su estado: el estado siempre es del turno.
- **El resumen para el asesor** pasa por las mismas revisiones y además no puede mencionar categorías
  protegidas. Si falla, la ficha sale sin resumen (DT-46).
- **Medido en vivo (8-oct):** 6% de turnos regenerados y 0,4% degradados en la demo × 5.

## 8. Marcadores: el código escribe los datos

```mermaid
sequenceDiagram
    participant S as Sonnet
    participant C as catálogo (tool)
    participant K as código (markers.py)
    participant L as Lead
    S->>C: get_property(prop_00231)
    C-->>S: price 185000, bedrooms 2, status disponible
    S->>K: "It's {prop_00231.status} and costs {prop_00231.price}."
    K->>K: ¿todos los marcadores tienen dato de una tool o de la memoria?
    K->>K: ¿algún número escrito a mano que no sea del lead ni de esa propiedad?
    K-->>L: "It's available and costs $185,000."
```

- **El modelo no puede equivocarse en un precio porque no lo escribe** (DT-60): escribe la referencia y el
  campo, y el código pone el valor.
- **Un marcador que no se puede completar** (una propiedad o un campo que ninguna tool dio) bloquea la
  respuesta.
- **Un número escrito a mano** pasa solo si lo dijo el lead, si es la numeración de una lista o si es el dato
  de la propiedad que nombra la línea. Si es dato de dos propiedades, la corrección le pide elegir el
  marcador.
- **Antes de DT-60** la comparación era literal (DT-23): necesitaba una excepción por redacción y no veía el
  precio real de *otra* propiedad.

## 9. FSM: el embudo

```mermaid
stateDiagram-v2
    [*] --> GREETING
    GREETING --> DISCOVERY
    DISCOVERY --> QUALIFY: buscó o miró una propiedad
    QUALIFY --> RECOMMEND: primer dato de calificación
    RECOMMEND --> SCHEDULE
    GREETING --> SCHEDULE: pide la visita
    DISCOVERY --> SCHEDULE
    QUALIFY --> SCHEDULE
    NURTURE --> SCHEDULE
    SCHEDULE --> HANDOFF: visita reservada
    DISCOVERY --> NURTURE: 2 seguimientos sin respuesta
    NURTURE --> DISCOVERY: el lead vuelve
    NURTURE --> LOST: sin respuesta
    GREETING --> HUMAN_HANDOFF: pide una persona · fuera de alcance · frustración
    DISCOVERY --> HUMAN_HANDOFF
    QUALIFY --> HUMAN_HANDOFF
    RECOMMEND --> HUMAN_HANDOFF
    SCHEDULE --> HUMAN_HANDOFF
    HANDOFF --> [*]
    HUMAN_HANDOFF --> [*]
```

- **9 estados** (`fsm.py`, DT-12). `HUMAN_HANDOFF` se alcanza desde cualquier estado activo.
- **Avanza por lo que pasó, no por etiquetas** (DT-61): buscar o mirar una propiedad cierra `DISCOVERY`; el
  primer dato de calificación (presupuesto, cómo se decide, cuándo o `check_requirements`) cierra `QUALIFY`.
- **`SCHEDULE` desde cualquier estado activo** si hay una propiedad visitable (DT-61, DT-67). Es la
  conversión principal: el lead que llega del anuncio pide la visita sin buscar antes.
- **`NURTURE` y `LOST` los mueve el *scheduler***, no un mensaje (§14).
- **Una suite de tests cubre cada arista** (OBS-3).

## 10. Score BANT-E

```mermaid
flowchart LR
    CL["Clasificador:<br/>señales del texto"] --> SIG["ScoreSignal<br/>sin campo para<br/>categorías protegidas"]
    TOOLS["Tools del turno:<br/>search_properties<br/>check_requirements"] --> SIG
    SIG --> F["Función pura de Python<br/>5 dimensiones × 20 pts"]
    F --> B["Budget: declaró presupuesto → 20"]
    F --> A["Authority: declaró cómo decide → 20"]
    F --> N["Need fit: 0 / 1-2 / 3+ resultados → 0 / 10 / 20"]
    F --> T["Timing: inmediata 20 · 3 meses 12 · explorando 5"]
    F --> E["Eligibility: cumple requisitos → 20"]
    B --> TOT["Total 0-100<br/>COLD ≤ 40 · WARM 41-70 · HOT ≥ 71"]
    A --> TOT
    N --> TOT
    T --> TOT
    E --> TOT
    TOT -->|"cruza a WARM o HOT"| CARD["Tarjeta al asesor<br/>lead_qualified = true"]
```

- **El LLM nunca pone la nota:** extrae señales; el número lo calcula `scoring.py`, con su razón por
  dimensión (`reasons`).
- **Need fit y Eligibility salen de las tools, no del LLM**: cuántas propiedades encontró la búsqueda y si
  `check_requirements` dio que cumple.
- **Fair housing es estructural:** `ScoreSignal` no tiene dónde guardar nacionalidad, religión o familia.
  `decision_maker` dice *si* el lead declaró cómo decide, no *con quién* (DT-38, DT-40).
- **Gold set de perfiles pareados** (C-6): 54 pares iguales salvo el dato protegido, diferencia 0 en todos.
  Corre en CI.
- **Calificar** (WARM o HOT) libera la dirección exacta y manda la tarjeta al asesor.

## 11. Agenda de visitas

```mermaid
sequenceDiagram
    participant L as Lead
    participant T as core · turno
    participant CL as Clasificador
    participant MCP as catalog · /mcp
    participant AG as catalog · /scheduling
    L->>T: "Great, can I go see it?"
    T->>CL: clasificar
    CL-->>T: wants_visit, refers_to = prop_00231, visit_when
    T->>MCP: get_availability: ¿visitable?
    T->>AG: assign_advisor(lead)
    T->>AG: get_slots(asesor, filtro de día y franja)
    AG-->>T: 3 horarios, lunes a sábado 9:00-18:00
    T-->>L: "1) Mon 10:00 2) … 3) …"
    L->>T: "1"
    T->>AG: book(idempotency_key)
    AG-->>T: reservada
    T-->>L: "… is booked for …"
    Note over T: FSM → HANDOFF · recordatorio el día anterior a las 18:00
```

- **La agenda la maneja el código, no el LLM** (DT-32, DT-39): las rutas de `/scheduling` no son tools MCP,
  así que el modelo no puede reservar ni inventar horarios.
- **`book` es idempotente** y la base impide dos visitas del mismo asesor a la misma hora.
- **Una propiedad apartada o cerrada no se agenda**: se lo dice y ofrece alternativas (DT-67).
- **Si la agenda no responde**, el lead deja su horario preferido y el asesor lo ve en la ficha.

## 12. Traspaso a humano

```mermaid
flowchart LR
    P1["El lead pide una persona<br/>o toca 'Talk to an agent'"] --> HH["HUMAN_HANDOFF"]
    P2["Fuera del alcance del agente"] --> HH
    P3["3 turnos sin resolver<br/>+ frustrated"] --> HH
    P4["Visita reservada"] --> H["HANDOFF"]
    HH --> R["Ruteo: asesor por zona,<br/>idioma y reparto por turnos"]
    H --> R
    R --> S["Resumen del LLM<br/>revisado por los guardrails"]
    S --> F["Ficha: score, resumen,<br/>transcripción, visita"]
    F --> PANEL["Aviso en el panel"]
    F -.-> TG["Telegram del asesor<br/>si registró su chat"]
```

- **El humano siempre está a un mensaje o a un botón**, desde cualquier estado.
- **El asesor no lee el chat entero:** lee el resumen (qué busca, objeciones, siguiente paso) y la nota
  (A-16, DT-46).
- **El ruteo lo decide la agenda** (`assign_advisor`, DT-33); si no responde, una regla local por zona.
- **Los avisos son de mejor esfuerzo:** si Telegram falla, el lead igual aparece en la cola del panel.

## 13. Catálogo MCP

```mermaid
flowchart LR
    S["Sonnet"] -->|"tool call"| W1
    subgraph core["core: envoltorios del catálogo"]
        W1["SearchLog<br/>búsquedas sin resultado (N-5)"] --> W2["AvailabilityGuard<br/>revalida el estado (A-4)"]
        W2 --> W3["Alternatives<br/>un filtro menos si no hay nada"]
    end
    W3 -->|"MCP streamable HTTP /mcp"| CAT
    subgraph CAT["catalog"]
        T1["search_properties"]
        T2["get_property"]
        T3["get_availability"]
        T4["get_promotions"]
        T5["check_requirements"]
        T6["list_zones"]
    end
    CAT --> PG[("properties · promotions")]
```

- **6 tools con contrato** (`contracts/mcp/`, CT-2): el servidor publica el mismo `inputSchema` contra el que
  valida (#182).
- **Streamable HTTP, no `stdio`** (DT-2): `core` y `catalog` son contenedores separados.
- **La disponibilidad se revalida** después de cada búsqueda o ficha (A-4): nunca se ofrece algo apartado o
  cerrado como disponible.
- **Una búsqueda vacía trae alternativas** con un filtro menos, que busca el código (DT-57).
- **`list_zones`** contesta la cobertura sin inventar barrios (DT-58).
- **`address_exact`** la redacta el catálogo si `lead_qualified` es falso.

## 14. Scheduler: tareas diferidas

```mermaid
flowchart LR
    LOOP["Bucle en core<br/>cada 30 s"] --> Q[("scheduled_jobs<br/>pending · done · failed · cancelled")]
    Q --> J1["no_response<br/>24 h de silencio → seguimiento<br/>2 sin respuesta → NURTURE → LOST"]
    Q --> J2["visit_reminder<br/>18:00 del día anterior"]
    Q --> J3["alert_scan<br/>busca propiedades nuevas"]
    J3 --> J4["property_alert<br/>una por alerta y propiedad"]
    Q --> J5["weekly_report<br/>lunes 9:00 al gerente"]
    STOP["/stop"] -.->|cancela| Q
    MSG["Mensaje del lead"] -.->|"reinicia el silencio"| J1
```

- **Corre dentro de `core`, sin Redis ni cola externa** (DT-3, DT-44): una tabla en Postgres.
- **El reporte semanal** son dos clases: `weekly_report_plan` arma un *job* `weekly_report` por destinatario.
- **Un solo pendiente por clase y `dedupe_key`**; ningún *job* corre dos veces; los fallidos se reintentan 2
  veces.
- **Los mensajes del *scheduler* no usan LLM**, salvo que se revisan con los guardrails de salida antes de
  mandar una alerta.
- **Todo respeta el consentimiento:** `/stop` cancela lo pendiente y desactiva la alerta.

## 15. Panel y landing

```mermaid
flowchart TB
    subgraph Publico["Sin token"]
        LAND["/properties<br/>propiedades disponibles<br/>enlace a Telegram por propiedad"]
    end
    subgraph Asesor["/panel/{token}"]
        COLA["Cola por score<br/>HOT arriba · SLA de 30 min"]
        FICHA["Ficha: score, resumen,<br/>transcripción, visita"]
        CERRAR["Cerrar operación"]
        AVISOS["Avisos"]
    end
    subgraph Gerencia["Cualquier token"]
        FUN["Funnel"]
        NEG["Negocio: colocación, rotación,<br/>estancadas, demanda, atribución"]
    end
    COLA --> FICHA --> CERRAR
    CERRAR -->|"deal + propiedad cerrada"| NEG
    LAND -->|"t.me/bot?start=prop_…"| TG["Telegram"]
```

- **FastAPI con plantillas, leyendo Postgres.** La autenticación es un token por asesor (DT-5); cada asesor
  ve los leads de su zona y los asignados (DT-42).
- **La landing es una ruta más del panel**, de solo lectura y sin datos sensibles: nunca `address_exact` ni
  leads (DT-71).
- **Cerrar una operación** escribe en una transacción el `deal`, el lead en `closed_won` y la propiedad en
  `cerrado` con su evento (N-3, DT-52).
- **Atribución al bot** (DT-41): un cierre es del bot si el lead tocó esa propiedad con el bot en los 60
  días previos.
- **Historia sintética** de seis meses para la demo, marcada `synthetic` y excluida de la cola y del funnel
  (N-9, DT-49).

## 16. Datos

```mermaid
erDiagram
    LEAD ||--o{ CONVERSATION : tiene
    CONVERSATION ||--o{ MESSAGE : guarda
    LEAD ||--o| LEAD_SCORE : "se califica"
    LEAD ||--o{ VISIT : agenda
    ADVISOR ||--o{ VISIT : atiende
    PROPERTY ||--o{ VISIT : "se visita"
    PROPERTY ||--o{ PROMOTION : tiene
    PROPERTY ||--o{ PROPERTY_STATUS_EVENT : historial
    LEAD ||--o{ PROPERTY_INTEREST : toca
    PROPERTY ||--o{ PROPERTY_INTEREST : "es tocada"
    PROPERTY ||--o{ DEAL : "se cierra"
    LEAD ||--o{ DEAL : compra
    ADVISOR ||--o{ ADVISOR_NOTIFICATION : recibe
    LEAD ||--o| PROPERTY_ALERT : pide
    LEAD ||--o{ SCHEDULED_JOB : "programa"
    LEAD ||--o{ FUNNEL_EVENT : "avanza"
```

- **Postgres como fuente de verdad desde el día 1** (DT-1). `pgvector` está instalado para la FAQ (C-5), que quedó como trabajo
  futuro.
- **Solo agregados sin texto del lead** en las tablas de negocio: `property_interest`, `unmet_searches`,
  `property_alerts`.
- **`property_status_events`**: el estado de una propiedad solo cambia por `db/property_history.py` (N-1).
- **`audit_log`**: guardrails, intentos de abuso y la decisión de cada turno. **`funnel_events`**:
  *append-only*, una fila por transición real (DT-17).

## 17. Llamadas al LLM: cuota y reintentos

```mermaid
flowchart LR
    CALL["Llamada al modelo"] --> RL{"Limitador local<br/>10 por minuto y modelo"}
    RL -->|"hay cupo"| BR["Bedrock Converse"]
    RL -->|"sin cupo"| WAIT["Espera en cola<br/>hasta LLM_MAX_QUEUE_WAIT_SECONDS<br/>15 s, 60 en la demo"]
    WAIT -->|"se libera"| BR
    WAIT -->|"no alcanza"| FB["Texto de respaldo<br/>el turno cuenta como sin resolver"]
    BR -->|"ThrottlingException<br/>u otro transitorio"| RET["Reintento con espera<br/>presupuesto por turno"]
    RET --> BR
    RET -->|"se agota"| FB
    BR -->|ok| OK["Respuesta"]
```

- **10 llamadas por minuto por modelo, fijas** (DT-6, DT-19). Un turno usa unas 2,4: el sistema aguanta unos
  4 turnos por minuto en total.
- **Ventana deslizante en memoria con cola por orden de llegada**, sin Redis. Es por proceso: el compose
  corre un solo *worker*.
- **Reintentos con presupuesto compartido por turno** (A-14, DT-30): un turno no puede quedar reintentando
  sin fin.
- **Sin LLM no se inventa nada:** el respaldo es un texto seguro (A-13).
- **Costo medido:** menos de 2 centavos de dólar por turno; el 96% son tokens de entrada.

## 18. Observabilidad

```mermaid
flowchart LR
    T["turn"] --> GI["guardrail.input"]
    T --> LLM["Llamadas a Bedrock<br/>OpenInference: tokens por modelo"]
    T --> MCP["catalog.tool<br/>llamadas MCP"]
    T --> FSM["fsm.advance<br/>estado, trigger, score"]
    T --> AR["turn.after_reply"]
    GI -->|OTLP| PHX["Phoenix"]
    LLM -->|OTLP| PHX
    MCP -->|OTLP| PHX
    FSM -->|OTLP| PHX
    AR -->|OTLP| PHX
    FSM --> AUD[("audit_log<br/>turn.decision")]
    FSM --> FE[("funnel_events")]
    LOGS["Logs JSON<br/>3 servicios"] --> STD["stdout"]
```

- **Phoenix autoalojado con OpenTelemetry** (DT-9), con la instrumentación de OpenInference para Bedrock y
  MCP.
- **Trazas por turno sin el texto de los leads** (DT-18): ruta, intención, flags, tools y violaciones por
  tipo, nunca el valor. En la UI de Phoenix, la entrada y la salida de las llamadas a Bedrock salen como
  `__REDACTED__`: es a propósito, no una falla. **El texto vive en Postgres** (`messages`, ya filtrado, y la
  decisión de cada turno en `audit_log`), y se lee en la ficha del panel o con `scripts/demo/exportar.py`.
  `TRACE_CONTENT=1` lo muestra también en Phoenix, solo para depurar con datos de prueba.
- **Los tokens de cada corrida** salen de Phoenix con `harness/scripts/tokens.py`.
- **Logs JSON** en los tres servicios (DT-27). Ningún log imprime texto del lead.
- **Plan B:** Jaeger recibe las mismas trazas cambiando solo el *endpoint* OTLP (OBS-8, DT-31).

**Si preguntan:** *¿por qué las trazas están "vacías"?* No lo están: tienen tiempos, tokens, tools y
decisiones. Lo que falta es el texto de la conversación, que es un dato personal. Separarlo es la decisión:
Postgres es la fuente de verdad del contenido, con su control de acceso, y Phoenix se queda solo con
métricas.

## 19. Cómo se prueba

```mermaid
flowchart TB
    subgraph CI["En cada PR · sin LLM"]
        U["Unos 2 700 tests<br/>en total"]
        G["Gold sets como gate<br/>factualidad · marcadores · dirección<br/>fair housing · idioma · promesas"]
        CT["Contract tests<br/>CT-1, MCP, FSM"]
    end
    subgraph Docker["Arnés E2E · agente fake"]
        E2E["Guiones contra los 3 servicios"]
    end
    subgraph Vivo["Contra Bedrock real · fuera de CI"]
        EV["evaluate.py<br/>12 escenarios × 5"]
        JU["Sets de jurado J N M O<br/>y reservado P"]
        CG["Gold set del clasificador"]
    end
    CI --> Docker --> Vivo
```

- **CI corre sin LLM:** los gold sets usan la salida real del catálogo de demo y el extractor determinista.
  Un gold set nuevo que nadie registró hace fallar el registro (`test_gold_sets_gate.py`, DT-37).
- **La evaluación en vivo** mide con lo que quedó en Postgres (tools, guardrails, ruta, estado final) y no
  fija frases, así que vale con cualquier modelo que haga lo correcto.
- **El set reservado P** se corrió una sola vez, sin ajustar nada antes ni después: mide generalización.
  J, N, M y O se usaron para arreglar, así que solo miden regresión.
- **Límite de proceso:** CI no bloquea el merge en este plan de GitHub; el *gate* depende del acuerdo del
  equipo.
