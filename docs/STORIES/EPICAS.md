# Épicas

Cada épica agrupa historias de una misma área funcional. El objetivo, alcance y "por qué existe" de cada una vive aquí; el detalle de cada historia (criterios de aceptación, Definition of Done) vive en su propio archivo dentro de este directorio — ver [`README.md`](README.md) para el índice completo.

## E0 — Contratos y entorno

**Objetivo:** congelar la forma de datos entre componentes (evento canónico, tools MCP del catálogo, estado de la FSM) y levantar un esqueleto end-to-end 100% *fake* — antes de que exista una sola línea de lógica de negocio real.

**Por qué primero:** permite que un equipo de 2-3 personas trabaje en paralelo sin bloquearse negociando formatos de datos a mitad de camino, y encuentra los bugs de integración de infraestructura (montaje de servicios, transporte de red, *lifespan*) cuando arreglarlos cuesta minutos.

**Historias:** CT-1 a CT-9.

## E1 — Motor del agente

**Objetivo:** la máquina de estados de la conversación, el orquestador (memoria + *tool calling*), el *scoring* BANT-E, y los 4 guardrails de salida (factualidad, dirección reservada, promesas no autorizadas, fair housing) más el guardrail de entrada.

**Decisión de arquitectura:** el estado de la conversación (FSM, historial, score) se persiste en Postgres desde la primera historia que lo necesita (A-1) — no vive en memoria con un espejo agregado después. Ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §4.

**Historias:** A-1 a A-18.

## E2 — Canal y borde

**Objetivo:** el bot de Telegram, la conversión a/desde el evento canónico, la identidad del lead entre canales, y el panel interno de asesores (lista, ficha, cola priorizada, notificaciones, agenda).

**Historias:** B-1 a B-14.

## E3 — Datos y conocimiento

**Objetivo:** el Catalog Service real sobre Postgres (REST + MCP), el esquema de datos completo con migraciones, las reglas BANT-E, la FAQ con embeddings, y los *gold sets* de guardrails (fair housing, factualidad, dirección reservada).

**Decisión de organización:** cada guardrail (E1) nace junto con su *gold set* (E3) en la misma historia, no como historias separadas en el tiempo — ver las notas de A-5/A-6 y C-7/C-8.

**Historias:** C-1 a C-9 (incluye C-1a, C-1b).

## E4 — Observabilidad y calidad

**Objetivo:** trazas por turno, métricas del funnel, cobertura de transiciones de la FSM, *gate* de CI sobre los *gold sets*, *contract tests*, la suite E2E formal, logs estructurados con PII enmascarada, y un plan B de observabilidad.

**Historias:** OBS-1 a OBS-8.

## E5 — Cierre y demo

**Objetivo:** hardening final (solo *bugfix*, sin alcance nuevo), demo end-to-end ensayada en vivo, documento de arquitectura final, y documentación publicada y enlazada.

**Historias:** Z-1 a Z-4.

## E6 — Inteligencia comercial

**Objetivo:** que la inmobiliaria sepa cuánto colocó en un periodo, qué propiedades no rotan, cuáles se cerraron después de conversar con el bot y qué demanda no está cubriendo. Pedido por el profesor; el análisis de qué faltaba está en [`docs/DESIGN/propuestas.md`](../DESIGN/propuestas.md).

**Decisión de organización:** todo el esquema nuevo entra en una sola historia (N-1) y una sola migración, para que `core`, el panel y el *seed* trabajen en paralelo sin chocar en Alembic. Las consultas (N-6, N-7) y las vistas (N-8) son historias separadas que se coordinan por las firmas que fijan las consultas en su primer PR.

**Historias:** N-1 a N-10. Las otras propuestas de `propuestas.md` van en su épica natural: A-15 a A-18 en E1 y B-10 a B-14 en E2.

---

## Piso absoluto (referencia)

Si el tiempo se acorta, esto es lo mínimo que ya aprueba el proyecto: **CT-1..CT-9, A-1, A-3, A-4, A-5, A-6, A-9, A-12, B-1, B-5, B-7, C-1, C-1a, C-1b, C-3, C-4, C-7, C-9.**

Es la única copia de esta lista. Incluye A-3 y C-3, que no son objetivos en sí, porque A-4 depende de A-3, y B-5 y C-9 dependen de C-3 (ver `ROADMAP.md` §2).

Esta lista se decide en la planificación del Sprint 1, no se re-deriva reactivamente cerca del cierre.
