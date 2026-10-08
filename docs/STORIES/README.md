# Historias de usuario — índice

Manual de historias de usuario del proyecto: un archivo por historia (no una tabla gigante única — ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §0) para que cada una se pueda leer, enlazar y revisar de forma aislada. El objetivo, alcance y motivación de cada épica está en [`EPICAS.md`](EPICAS.md); la plantilla para agregar una historia nueva está en [`PLANTILLA-HISTORIA.md`](PLANTILLA-HISTORIA.md).

## Cómo se usa esto

- **Este directorio es el contenido de cada historia** (historia de usuario, criterios de aceptación, Definition of Done) — es la fuente de verdad de *qué* se va a construir y *cómo* se sabe que está terminado.
- **GitHub Issues + Project es la única fuente de verdad de *estado*** (To Do / In Progress / Done). Cada archivo puede enlazar a su Issue una vez creado, pero nunca repite ni cachea su estado aquí (ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5).
- Cuando una historia cambie de alcance de forma real (no solo de estado), el archivo se edita vía PR como cualquier otro documento versionado.
- **Definition of Done común a toda historia** (repetida en cada archivo, no se repite aquí para no duplicarla en dos lugares):
  1. Código en `main` vía PR revisado por al menos otra persona del equipo.
  2. Suite de tests del servicio/paquete afectado en verde en CI.
  3. Ningún *contract test* roto.
  4. Estado actualizado en GitHub Issues/Project.
  5. Cualquier decisión de arquitectura nueva registrada en `docs/DECISIONS/decisions.md`.

  Cada historia agrega, además, sus propios criterios específicos (p. ej. "gold set en verde", "verificado en vivo contra el sistema real").

## Convención de prioridad y puntos

| Concepto | Convención |
| :--- | :--- |
| **Prioridad** | `P0` (núcleo, no se recorta) · `P1` (mejora con *fallback* si no alcanza el tiempo) · `P2` (extensión, solo si sobra tiempo) |
| **Puntos** | Fibonacci `1·2·3·5·8` — una historia de 8 puntos se parte, salvo P2 sin alcance confirmado |
| **Duración** | `horas ideales = puntos × 2` (heurística estándar para puntos Fibonacci pequeños) — a la dedicación de referencia del equipo (~8-10 h/semana por persona) equivale aproximadamente a **1 punto ≈ 1 día-persona**. Es una estimación de partida: se recalibra con la velocidad real después de las primeras 1-2 semanas. |
| **Track** | `A` (motor del agente) · `B` (canal y borde) · `C` (datos y conocimiento) · `Cross` (todo lo demás) |

El orden recomendado para avanzar las historias con 3 personas en paralelo, minimizando conflictos de merge y tiempo bloqueado por dependencias, y el contraste entre el total del backlog y la capacidad real del equipo, están en [`ROADMAP.md`](ROADMAP.md).

## Índice completo

### E0 — Contratos y entorno

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`CT-1`](CT-01.md) | Congelar el contrato del evento de mensaje canónico | P0 | 2 | 4h | — |
| [`CT-2`](CT-02.md) | Congelar el contrato MCP del Catalog Service | P0 | 3 | 6h | — |
| [`CT-3`](CT-03.md) | Congelar el estado + contexto de la FSM | P0 | 2 | 4h | — |
| [`CT-4`](CT-04.md) | Esqueleto end-to-end con los 3 *fakes* conectados | P0 | 5 | 10h | CT-1, CT-2, CT-3, CT-6 |
| [`CT-5`](CT-05.md) | Repo, CI y estructura de carpetas | P0 | 3 | 6h | — |
| [`CT-6`](CT-06.md) | `docker-compose` base | P0 | 3 | 6h | — |
| [`CT-7`](CT-07.md) | Fake LLM y fake core | P0 | 2 | 4h | CT-1 |
| [`CT-8`](CT-08.md) | Fake catalog como servidor MCP en memoria | P0 | 3 | 6h | CT-2 |
| [`CT-9`](CT-09.md) | Arnés de canal | P0 | 2 | 4h | CT-1 |

### E1 — Motor del agente

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`A-1`](A-01.md) | Flujo FSM coherente | P0 | 5 | 10h | CT-4 |
| [`A-2`](A-02.md) | Respuestas naturales con memoria de conversación | P0 | 3 | 6h | A-1 |
| [`A-3`](A-03.md) | Cliente MCP para tools de catálogo | P0 | 3 | 6h | CT-8 |
| [`A-4`](A-04.md) | Recomendación con datos reales | P0 | 3 | 6h | A-3, C-1 |
| [`A-5`](A-05.md) | Guardrail de salida — factualidad | P0 | 3 | 6h | A-4 |
| [`A-6`](A-06.md) | Dirección exacta y datos reservados | P0 | 2 | 4h | A-5 |
| [`A-7`](A-07.md) | Guardrail de promesas no autorizadas | P0 | 3 | 6h | A-5 |
| [`A-8`](A-08.md) | Guardrail de entrada | P1 | 2 | 4h | A-1 |
| [`A-9`](A-09.md) | Scoring BANT-E integrado en la FSM | P0 | 2 | 4h | C-4 |
| [`A-10`](A-10.md) | Ruteo por zona al asesor | P1 | 2 | 4h | C-2 |
| [`A-11`](A-11.md) | Fair housing en el scoring | P0 | 3 | 6h | A-9, C-6 |
| [`A-12`](A-12.md) | Ficha del lead al asesor en HANDOFF | P0 | 3 | 6h | A-9, B-7 |
| [`A-13`](A-13.md) | `HUMAN_HANDOFF` desde cualquier estado | P1 | 1 | 2h | A-1 |
| [`A-14`](A-14.md) | Reintentos con backoff exponencial ante límites de Bedrock | P1 | 2 | 4h | A-2 |
| [`A-15`](A-15.md) | `need_fit` cuenta la propiedad del anuncio | P1 | 1 | 2h | — |
| [`A-16`](A-16.md) | Resumen de la conversación en la ficha del asesor | P2 | 3 | 6h | — |
| [`A-17`](A-17.md) | *Scheduler* de tareas diferidas | P2 | 5 | 10h | — |
| [`A-18`](A-18.md) | Alertas de propiedades nuevas | P2 | 3 | 6h | A-17 |

### E2 — Canal y borde

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`B-1`](B-01.md) | Bot de Telegram por *long polling* | P0 | 5 | 10h | CT-4 |
| [`B-2`](B-02.md) | Consentimiento antes de guardar teléfono/correo | P0 | 3 | 6h | B-1 |
| [`B-3`](B-03.md) | `to_canonical`/`from_canonical` para Telegram | P0 | 3 | 6h | CT-1, B-1 |
| [`B-4`](B-04.md) | Identidad del lead entre canales | P0 | 3 | 6h | B-3, C-3 |
| [`B-5`](B-05.md) | Panel interno con ficha, etapa y transcripción | P0 | 5 | 10h | C-3 |
| [`B-6`](B-06.md) | Cola priorizada por score en el panel | P1 | 3 | 6h | B-5, A-9 |
| [`B-7`](B-07.md) | Notificación al asesor con la tarjeta del lead | P0 | 3 | 6h | B-5 |
| [`B-8`](B-08.md) | Agendar visita vía el proveedor de calendario elegido | P1 | 3 | 6h | C-2 |
| [`B-9`](B-09.md) | Dashboard del funnel | P1 | 3 | 6h | OBS-1 |
| [`B-10`](B-10.md) | Foto de la propiedad en Telegram | P1 | 2 | 4h | N-1 |
| [`B-11`](B-11.md) | Botones "Agendar visita" y "Hablar con un asesor" | P1 | 3 | 6h | B-8 |
| [`B-12`](B-12.md) | SLA de atención del asesor | P2 | 2 | 4h | N-8 |
| [`B-13`](B-13.md) | Recordatorio de la visita el día anterior | P2 | 1 | 2h | B-8, A-17 |
| [`B-14`](B-14.md) | Notas de voz transcritas (tarea futura, fuera del capstone) | P2 | 3 | 6h | — |
| [`B-15`](B-15.md) | Avisos al asesor en el panel, con Telegram opcional | P1 | 3 | 6h | — |
| [`B-16`](B-16.md) | Landing pública con enlace a Telegram por propiedad | P1 | 3 | 6h | B-1, B-10 |

### E3 — Datos y conocimiento

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`C-1`](C-01.md) | Catalog Service real (REST) | P0 | 5 | 10h | CT-2 |
| [`C-1a`](C-01A.md) | Resto de tools del catálogo | P0 | 3 | 6h | C-1 |
| [`C-1b`](C-01B.md) | Catalog Service expuesto como servidor MCP | P0 | 3 | 6h | C-1, C-1a |
| [`C-2`](C-02.md) | `assign_advisor`, `get_slots`, `book` | P1 | 3 | 6h | C-1, C-3 |
| [`C-3`](C-03.md) | Esquema de datos completo con migraciones | P0 | 5 | 10h | CT-3 |
| [`C-4`](C-04.md) | Reglas BANT-E probadas | P0 | 5 | 10h | — |
| [`C-5`](C-05.md) | FAQ con embeddings en pgvector (tarea futura, fuera del capstone) | P1 | 3 | 6h | CT-6 |
| [`C-6`](C-06.md) | Gold set de fair housing | P0 | 3 | 6h | C-4 |
| [`C-7`](C-07.md) | Gold set de factualidad ≥30 preguntas | P0 | 3 | 6h | C-1, C-1a |
| [`C-8`](C-08.md) | Gold set de dirección reservada | P0 | 2 | 4h | C-1 |
| [`C-9`](C-09.md) | Inventario de demo realista | P0 | 3 | 6h | C-1, C-1a, C-3 |

### E4 — Observabilidad y calidad

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`OBS-1`](OBS-01.md) | Traza por turno | P0 | 5 | 10h | A-1, CT-6 |
| [`OBS-2`](OBS-02.md) | Métricas del funnel en Postgres | P0 | 3 | 6h | A-1, C-3 |
| [`OBS-3`](OBS-03.md) | Suite de transiciones de la FSM | P0 | 3 | 6h | A-1 |
| [`OBS-4`](OBS-04.md) | Gold sets como *gate* de merge | P0 | 3 | 6h | C-6, C-7, C-8 |
| [`OBS-5`](OBS-05.md) | Contract tests en CI | P0 | 3 | 6h | CT-1, CT-2, CT-3 |
| [`OBS-6`](OBS-06.md) | Conversaciones E2E contra el bot real | P1 | 3 | 6h | B-1, A-12 |
| [`OBS-7`](OBS-07.md) | `/health`, logs estructurados, PII enmascarada | P1 | 2 | 4h | CT-5 |
| [`OBS-8`](OBS-08.md) | Plan B de observabilidad documentado | P2 | 1 | 2h | OBS-1 |

### E5 — Cierre y demo

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`Z-1`](Z-01.md) | Semana de hardening | P0 | 5 | 10h | OBS-4 |
| [`Z-2`](Z-02.md) | Demo end-to-end guionizada en vivo | P0 | 3 | 6h | OBS-6 |
| [`Z-3`](Z-03.md) | Documento de arquitectura final | P0 | 3 | 6h | — |
| [`Z-4`](Z-04.md) | Documentación publicada y enlazada | P1 | 1 | 2h | Z-3 |

### E6 — Inteligencia comercial

| Historia | Título | Prioridad | Puntos | Duración | Depende |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`N-1`](N-01.md) | Esquema de historial, operaciones, interés y demanda | P1 | 3 | 6h | — |
| [`N-2`](N-02.md) | Regla de atribución y visibilidad de las métricas | P1 | 1 | 2h | — |
| [`N-3`](N-03.md) | Cerrar operación desde la ficha del lead | P1 | 3 | 6h | N-1 |
| [`N-4`](N-04.md) | Registrar el interés del lead por cada propiedad | P1 | 2 | 4h | N-1 |
| [`N-5`](N-05.md) | Campaña de origen y demanda insatisfecha | P1 | 2 | 4h | N-1 |
| [`N-6`](N-06.md) | Consultas de inventario y rotación | P1 | 3 | 6h | N-1 |
| [`N-7`](N-07.md) | Consultas de atribución al bot y conversión | P1 | 3 | 6h | N-1, N-2 |
| [`N-8`](N-08.md) | Vista de inventario y negocio en el panel | P1 | 3 | 6h | N-2, N-6, N-7 |
| [`N-9`](N-09.md) | Seis meses de historia sintética para la demo | P1 | 3 | 6h | N-1 |
| [`N-10`](N-10.md) | Reporte semanal al gerente por Telegram | P2 | 2 | 4h | N-6, N-7, A-17 |

## Piso absoluto

El mínimo que aprueba el proyecto está en [`EPICAS.md`](EPICAS.md#piso-absoluto-referencia).
