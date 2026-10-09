# Documentación — índice

Único índice de navegación de la documentación del proyecto. Todo vive bajo `docs/`; no hay un
segundo árbol de documentación.

| Documento | Para qué sirve |
| :--- | :--- |
| [`ONBOARDING.md`](ONBOARDING.md) | Tutorial paso a paso del flujo Git/GitHub del equipo |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Reglas de trabajo: ramas, Pull Requests, commits, cierre de Issues |
| [`ESTRUCTURA-DEL-PROYECTO.md`](ESTRUCTURA-DEL-PROYECTO.md) | Árbol de carpetas y qué responsabilidad tiene cada una |
| [`DEMO.md`](DEMO.md) | Guion de la demo en vivo, qué hace y qué no hace el agente por diseño, plan B y los ensayos medidos |
| [`evidencia/2026-10-08/`](evidencia/2026-10-08/README.md) | Informes de las corridas en vivo del cierre (demo × 5, set P, J a O), con tokens y costo |
| [`TESTING.md`](TESTING.md) | Levantar el stack, probar el bot, el panel, Bedrock y las trazas a mano, y correr cada suite de tests |
| [`../harness/README.md`](../harness/README.md) | Formato de los guiones del arnés E2E y qué valida en cada turno |
| [`DESIGN/design.md`](DESIGN/design.md) | Referencia compartida: entidades, estados de la FSM, reglas BANT-E, transporte MCP, y qué está construido de verdad (§5) |
| [`DESIGN/guardrails.md`](DESIGN/guardrails.md) | Guardrails de entrada y salida, *claims* prohibidos, estado de cobertura |
| [`DESIGN/panel-ui.md`](DESIGN/panel-ui.md) | Boceto de UI/UX del panel de asesores: sistema visual, componentes, navegación y comportamiento por vista (#178) |
| [`DESIGN/propuestas.md`](DESIGN/propuestas.md) | Funcionalidades propuestas para después del alcance actual (inventario y rotación, atribución al bot, demanda insatisfecha) y qué datos necesita cada una |
| [`DECISIONS/decisions.md`](DECISIONS/decisions.md) | Bitácora de decisiones de arquitectura (el *por qué*) |
| [`STORIES/README.md`](STORIES/README.md) | Índice de historias de usuario, prioridades y dependencias |
| [`STORIES/ROADMAP.md`](STORIES/ROADMAP.md) | Orden recomendado y reparto entre las 3 personas |
| [`STORIES/EPICAS.md`](STORIES/EPICAS.md) | Objetivo de cada épica |
| [`presentations/2026-09-24-revision-de-avance.md`](presentations/2026-09-24-revision-de-avance.md) | Presentación de avance del equipo: objetivo, arquitectura, backlog, próxima semana y guion de demo |
| [`presentations/2026-10-01-estado-del-proyecto.md`](presentations/2026-10-01-estado-del-proyecto.md) | Estado del proyecto al 1 de octubre: backlog, qué funciona en vivo, qué falta por persona, riesgos y cómo preparar la demo |
| [`presentations/2026-10-05-estado-del-proyecto.md`](presentations/2026-10-05-estado-del-proyecto.md) | Guion de la presentación del 5 de octubre: objetivo, próxima semana, backlog, historia típica, arquitectura, demo y preguntas. Sus diapositivas están en [`presentations/slides/`](presentations/slides/README.md) |
| [`presentations/2026-10-07-estado-del-proyecto.md`](presentations/2026-10-07-estado-del-proyecto.md) | Estado al 7 de octubre: inglés y Colombia, factualidad por construcción, evaluación en vivo y prueba de jurado, demo y objetivo de la semana de cierre |
| [`presentations/2026-10-07-prueba-de-jurado.md`](presentations/2026-10-07-prueba-de-jurado.md) | Confiabilidad frente a un jurado (#247): resultados de los sets J, N y M, la estrategia de campos del clasificador (DT-62 a DT-65), qué quedó hecho, qué sigue y cómo retomar con la base limpia |
| [`presentations/2026-10-07-plan-confiabilidad.md`](presentations/2026-10-07-plan-confiabilidad.md) | Plan de trabajo hasta la demo: principios (medir primero, un dueño por clase de error, el código decide), diagnóstico de O6, O9 y O2, pasos 0 a 5 y qué queda fuera |
| [`presentations/2026-10-08-cierre-de-evaluacion.md`](presentations/2026-10-08-cierre-de-evaluacion.md) | Cierre de la evaluación antes de la demo: qué código se midió, demo × 5 contra la línea base, set reservado P, regresión de J a O, gold set, costo y facturación de AWS, límites conocidos, la fase 2 pendiente y dónde está la evidencia (`docs/evidencia/2026-10-08/`) |
| [`presentations/2026-10-09-estado-del-proyecto.md`](presentations/2026-10-09-estado-del-proyecto.md) | Estado al 9 de octubre: backlog cerrado (0 historias abiertas), prueba de jurado y set reservado P, evaluación de cierre contra la línea base, landing pública, demo, qué queda y riesgos (créditos de AWS) |
| [`presentations/2026-10-09-arquitectura-por-piezas.md`](presentations/2026-10-09-arquitectura-por-piezas.md) | Un diagrama por pieza del sistema para responder preguntas de arquitectura: canal, turno, guardrails de entrada y salida, clasificador, agente, marcadores, FSM, score, agenda, traspaso, catálogo MCP, *scheduler*, panel, datos, cuota del LLM, observabilidad y pruebas |

El estado de cada historia (To Do / In Progress / Done) vive solo en GitHub Issues y en el
[Project](https://github.com/orgs/softserve-leads-inmobiliaria/projects/1), nunca en estos
archivos.
