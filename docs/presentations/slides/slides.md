---
theme: default
title: Agente de Leads Inmobiliarios
info: |
  Estado del proyecto al 5 de octubre de 2026. Equipo #2, SoftServe Project Lab.
  Guion completo: docs/presentations/2026-10-05-estado-del-proyecto.md
colorSchema: light
transition: slide-left
mdc: true
layout: cover
class: text-left
---

# Agente de Leads Inmobiliarios

Estado del proyecto · 5 de octubre de 2026

<div class="mt-12 text-sm opacity-80">
Equipo #2 · SoftServe Project Lab<br>
Santiago Martínez · David Barreto · Miguelangel Vélez
</div>

<!--
Presentarse en una frase. Avisar que hay demo en vivo a la mitad.
-->

---
layout: center
---

# Qué vamos a ver

<div class="grid grid-cols-2 gap-x-12 gap-y-3 mt-8 text-lg">

<div>① Objetivo del producto</div>
<div>⑤ Demo en vivo</div>
<div>② Cómo está el backlog</div>
<div>⑥ Puntos clave: calidad y riesgos</div>
<div>③ Una historia típica</div>
<div>⑦ Objetivo de la próxima semana</div>
<div>④ Arquitectura</div>
<div>⑧ Preguntas</div>

</div>

<!--
10 segundos. Son los puntos de la consigna; el objetivo de la semana va al final para contar primero dónde estamos y después qué sigue.
-->

---
layout: statement
---

# Los asesores responden **tarde** a los leads buenos y pierden su tiempo con los que **no van a comprar**

<!--
El problema en una frase. Pausa antes de pasar a la solución.
-->

---

# ① Objetivo del producto

<div class="text-xl leading-relaxed mt-4 mb-6 border-l-4 border-teal-600 pl-4">
Un agente en <b>Telegram</b> que atiende al interesado a cualquier hora, lo <b>califica</b>, le recomienda
propiedades <b>reales</b>, le <b>agenda la visita</b> y se lo entrega al asesor listo para cerrar.
</div>

```mermaid {scale: 0.72}
flowchart LR
    L([Lead que llega<br/>del anuncio]) --> AG[Agente en<br/>Telegram]
    AG -->|busca| CAT[(Catálogo real)]
    AG -->|califica| SC[Score BANT-E<br/>COLD · WARM · HOT]
    AG -->|agenda| VIS[Visita]
    SC -->|WARM / HOT| ADV([Asesor<br/>de la zona])
    ADV --> N[Panel y métricas<br/>de negocio]
```

<!--
Leer la frase destacada. Recorrer el diagrama de izquierda a derecha: el lead llega del anuncio y termina en una métrica de negocio.
-->

---

# Tres reglas que no se negocian

<div class="grid grid-cols-3 gap-6 mt-10">

<div v-click class="card">
<div class="text-4xl mb-3">🔎</div>

### El LLM nunca inventa

Precios, disponibilidad y direcciones salen del catálogo. Los guardrails revisan cada respuesta antes de
enviarla.
</div>

<div v-click class="card">
<div class="text-4xl mb-3">⚖️</div>

### El LLM nunca pone la nota

Solo extrae señales. El score lo calcula Python, auditable, y no ve nacionalidad, estado civil ni familia.
</div>

<div v-click class="card">
<div class="text-4xl mb-3">🙋</div>

### El humano está a un botón

Cualquiera puede pedir un asesor en cualquier momento de la conversación.
</div>

</div>

<!--
Estas tres reglas explican casi todas las decisiones de arquitectura. Volvemos a ellas en la demo.
-->

---
layout: fact
---

# 71 de 75

historias hechas · **203 de 217 puntos** entregados

<div class="text-base opacity-75 mt-6">
3 abiertas (C-5, Z-1, Z-2) · 1 descartada (B-14, notas de voz)<br>
Desde el 1 de octubre: 23 historias y 12 bugs cerrados en 38 PRs
</div>

<!--
② Backlog. El número grande primero; el detalle va en la siguiente.
-->

---

# ② Cómo está el backlog

<div class="grid grid-cols-[3fr_2fr] gap-8">
<div class="text-sm">

| Épica | Hechas | Abiertas |
| :--- | ---: | :--- |
| **E0** Contratos y entorno | 9 / 9 | — |
| **E1** Motor del agente | 18 / 18 | — |
| **E2** Canal y panel | 14 / 15 | B-14 descartada |
| **E3** Datos y conocimiento | 10 / 11 | C-5 FAQ (en revisión) |
| **E4** Observabilidad y calidad | 8 / 8 | — |
| **E5** Cierre y demo | 2 / 4 | Z-1, Z-2 |
| **E6** Inteligencia comercial | 10 / 10 | — |

</div>
<div class="text-sm">

**Cómo lo organizamos**

- Cada historia es un issue con épica, track, prioridad P0-P2 y puntos (1 punto ≈ 2 h).
- El tablero de GitHub Projects es la **única** fuente de estado.
- 3 tracks paralelos: **core** (motor), **borde** (canal y panel) y **datos** (catálogo).
- E6 la pidió el profesor y se completó entera esta semana.

</div>
</div>

<!--
Mostrar el tablero en vivo si hay tiempo (10 s). Destacar que E6 se agregó y cerró en una semana.
-->

---

# ③ Una historia típica: B-11

<div class="grid grid-cols-2 gap-8">
<div>

<div class="text-lg border-l-4 border-teal-600 pl-4 mb-4">
<b>Como</b> lead, <b>quiero</b> botones para agendar una visita o hablar con un asesor,
<b>para</b> no tener que escribirlo.
</div>

<div class="text-xs opacity-75 mb-3">E2 · P1 · 3 puntos · track borde · depende de B-8</div>

**Criterios de aceptación**

<div class="text-sm">

- "Hablar con un asesor" siempre; "Agendar visita" **solo** donde funciona.
- El botón es un mensaje con texto fijo: **no se salta los guardrails**.
- El contrato del evento (CT-1) no cambia.
- Tocar dos veces no duplica la reserva.

</div>
</div>
<div class="text-sm">

**Definition of Done**, igual para todas

- PR revisado por otra persona
- Suite en verde en CI
- Ningún *contract test* roto
- Decisión registrada si cambia la arquitectura
- **Verificado en vivo en Telegram**

**Recorrido real**

Issue #137 → rama → PR #185 → decisión **DT-51** → merge el 4 de octubre

</div>
</div>

<!--
Elegimos B-11 porque recorre todo el ciclo y porque se ve en la demo. Remarcar "verificado en vivo": varios bugs de esta semana solo aparecieron así.
-->

---

# ④ Arquitectura

```mermaid {scale: 0.62}
flowchart LR
    LEAD([Lead]) <-->|Telegram: texto,<br/>foto, botones| CORE
    ASESOR([Asesor]) -->|navegador| PANEL

    subgraph Stack["docker compose · todo local"]
        CORE["core<br/>FSM · guardrails · score<br/>agente · scheduler"]
        CAT["catalog<br/>servidor MCP + agenda"]
        PANEL["panel<br/>cola · ficha · avisos<br/>funnel · negocio"]
        PG[("Postgres<br/>+ pgvector")]
        PHX["Phoenix<br/>trazas"]
    end

    BR["Amazon Bedrock<br/>Claude Haiku 4.5"]

    CORE -->|MCP| CAT
    CORE <--> BR
    CORE --> PG
    CAT --> PG
    PANEL --> PG
    CORE -.->|OTLP| PHX
```

<div class="text-sm mt-2 opacity-80">
El agente solo ve lo que devuelven las tools del catálogo (MCP). Todo el estado vive en Postgres.
Telegram por <i>long polling</i>: sin URL pública. El gerente recibe un reporte semanal por Telegram.
</div>

<!--
Orden de lectura: lead → core → catálogo y Bedrock → Postgres → panel del asesor. Phoenix guarda una traza por turno.
-->

---

# Qué pasa en cada mensaje

<div class="flow mt-10">
<div class="flow-step"><b>Guardrail de entrada</b><span>redacta PII y datos protegidos</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step"><b>LLM extrae señales</b><span>no decide nada</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step flow-hl"><b>Score BANT&#8209;E</b><span>lo calcula Python</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step"><b>FSM</b><span>9 estados</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step"><b>Agente</b><span>solo ve las tools MCP</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step flow-hl"><b>Guardrails de salida</b><span>si no pasa, regenera</span></div>
<div class="flow-arrow">→</div>
<div class="flow-step"><b>Respuesta</b><span>texto, foto y botones</span></div>
</div>

<div class="grid grid-cols-2 gap-8 mt-8 text-sm">
<div v-click>

**Entrada:** PII, *jailbreak* y menciones protegidas ("soy colombiano") se redactan antes de que nada
las vea.

</div>
<div v-click>

**Salida:** factualidad contra el catálogo, dirección reservada, promesas no autorizadas y disponibilidad
revalidada.

</div>
</div>

<!--
Aquí se ven las tres reglas: el score lo calcula Python, no el LLM; y nada sale sin pasar los guardrails.
-->

---
layout: center
class: text-center
---

# ⑤ Demo en vivo

<div class="text-lg opacity-80 mt-4">Telegram (lead) · Panel (asesor)</div>

<div class="text-sm mt-10 grid grid-cols-3 gap-4 text-left">
<div>1. Entra por el anuncio: foto y zona</div>
<div>2. Se califica hasta HOT</div>
<div>3. "Soy colombiano": guardrail</div>
<div>4. "¿La dirección exacta?": guardrail</div>
<div>5. Botón "Agendar visita"</div>
<div>6. Botón "Hablar con un asesor"</div>
<div>7. Panel: cola y resumen</div>
<div>8. Cerrar la operación</div>
<div>9. Vista de negocio</div>
</div>

<!--
Antes: make demo, LLM_PROVIDER=bedrock, dos pantallas.
Enlace: t.me/<bot>?start=prop_00231
Mensajes: "¿Sigue disponible? Tengo 2.5 millones, decido yo y quiero mudarme este mes" → "¿Qué requisitos piden?" → "Soy colombiano, ¿eso influye?" → "¿Me pasas la dirección exacta?" → botones.
Plan B sin Telegram: make conversar ARGS="04_deep_link_hasta_hot". Sin Bedrock: make harness.
-->

---

# ⑥ Puntos clave: calidad que se puede comprobar

<div class="grid grid-cols-4 gap-4 mt-8 text-center">
<div class="kpi"><div class="kpi-n">+1 000</div><div class="kpi-l">tests en 7 suites, en cada PR</div></div>
<div class="kpi"><div class="kpi-n">27 / 27</div><div class="kpi-l">perfiles de fair housing con diferencia 0 en el score</div></div>
<div class="kpi"><div class="kpi-n">75</div><div class="kpi-l">casos de factualidad contra el catálogo real</div></div>
<div class="kpi"><div class="kpi-n">53</div><div class="kpi-l">decisiones de arquitectura registradas</div></div>
</div>

<div class="mt-10 text-sm">

- Los **gold sets** son un *gate*: si uno baja de su umbral, CI falla.
- Los 10 contratos entre servicios tienen *contract tests*.
- La corrida E2E real contra los 3 servicios con Bedrock no cayó nunca a la respuesta de respaldo.
- **Lo que aprendimos:** los bugs de esta semana (Markdown literal, aviso repetido, lead que no llegaba a
  HOT) los encontraron las pruebas en vivo, no los tests. Por eso "verificado en vivo" está en el DoD.

</div>

<!--
El mensaje: no pedimos que nos crean, se puede comprobar en CI.
-->

---

# Riesgos y cómo los cubrimos

<div class="text-sm mt-6">

| Riesgo | Mitigación |
| :--- | :--- |
| Cambiar el contrato del catálogo (#188, tipo de inmueble) en la semana de cierre | Filtro opcional y retrocompatible, con *contract tests* y su decisión registrada |
| El cupo de Bedrock en la demo | Límite de tasa local, reintentos con *backoff* y plan B sin Bedrock |
| CI no bloquea el merge (repo privado, plan gratuito) | Acuerdo de equipo: nunca se mergea en rojo |
| Tres *gaps* abiertos en una sola persona | Repartirlos al arrancar la semana |

</div>

<!--
Mostrar que conocemos los riesgos y que cada uno tiene dueño o plan.
-->

---

# ⑦ Objetivo de la próxima semana

<div class="text-xl leading-relaxed mt-4 mb-8 border-l-4 border-teal-600 pl-4">
Alcance congelado y la demo de punta a punta corriendo en vivo, con Bedrock y Telegram reales,
<b>tres veces seguidas sin fallar</b>.
</div>

<div class="text-sm">

| | Qué | Prioridad |
| :--- | :--- | :--- |
| **C-5** | FAQ con embeddings en pgvector (PR en revisión, CI en verde) | P1 |
| #194 | El agente sabe en qué zonas hay inventario | P1 |
| #188 | Buscar por tipo de inmueble: depa, casa, terreno | P1 |
| #178 | Rediseño visual del panel | P1 |
| **Z-1** | Semana de *hardening*: solo *bugfix* | P0 |
| **Z-2** | Demo guionizada y ensayada | P0 |

</div>

<!--
Lo pusimos al final a propósito: primero dónde estamos, después qué sigue. Criterio de terminado: los 4 escenarios de make conversar y el guion de la demo pasan con Bedrock, sin respuestas de respaldo.
-->

---
layout: center
class: text-center
---

# ⑧ Preguntas

<div class="text-sm opacity-75 mt-6">
Repositorio y guion completo: <code>docs/presentations/2026-10-05-estado-del-proyecto.md</code>
</div>

<!--
Respuestas preparadas:
- ¿Y si el LLM inventa un precio? La factualidad compara cada número con lo que devolvieron las tools; si no coincide regenera, y si vuelve a fallar manda una respuesta segura. El dato inventado nunca sale.
- ¿Cómo evitan la discriminación? El LLM solo extrae señales y Python pone la nota. Las menciones protegidas se redactan a la entrada; 27 perfiles pareados exigen diferencia 0 para poder mergear.
- ¿Por qué Telegram? Long polling: todo local, sin URL pública (DT-4). WhatsApp sería otro adaptador detrás del evento canónico (DT-20).
- ¿Por qué MCP? El agente solo ve lo que devuelven las tools: eso hace verificable la factualidad y permite probar con un catálogo fake.
- ¿Modelo y costo? Bedrock, dos llamadas por turno (responder y extraer señales, DT-6), hoy Haiku 4.5; límite de tasa local y reintentos (DT-19, DT-30).
- ¿Cómo saben que el bot vende? DT-41: un cierre es del bot si el lead tocó esa propiedad con el bot en los 60 días previos.
- ¿Con más tiempo? Notas de voz (B-14), WhatsApp y búsqueda semántica en las descripciones.
-->
