# Documentación — índice

Único índice de navegación de la documentación del proyecto. Todo vive bajo `docs/`, en tres niveles:

- **Esta carpeta:** lo que sirve para entender el producto, cómo está construido y cómo correrlo.
- **[`internal/`](internal/README.md):** lo que usa el equipo para trabajar (historias, guía de Git,
  presentaciones vigentes, evidencia de las corridas).
- **[`archive/`](archive/README.md):** documentos superados. Se conservan como registro y no se actualizan.

## Para entender el sistema

| Documento | Para qué sirve |
| :--- | :--- |
| [`DESIGN/arquitectura-por-piezas.md`](DESIGN/arquitectura-por-piezas.md) | Un diagrama por pieza del sistema: canal, turno, guardrails, clasificador, agente, marcadores, FSM, score, agenda, traspaso, catálogo MCP, *scheduler*, panel, datos, cuota del LLM, observabilidad y pruebas. **Empezar por acá** |
| [`DESIGN/design.md`](DESIGN/design.md) | Referencia compartida: entidades, estados de la FSM, reglas BANT-E, transporte MCP, y qué está construido de verdad (§5) |
| [`DESIGN/guardrails.md`](DESIGN/guardrails.md) | Guardrails de entrada y salida, *claims* prohibidos, estado de cobertura y cómo se prueba cada uno |
| [`DECISIONS/decisions.md`](DECISIONS/decisions.md) | Bitácora de decisiones de arquitectura (DT-1 a DT-71): el *por qué* de cada elección |
| [`DESIGN/panel-ui.md`](DESIGN/panel-ui.md) | UI/UX del panel de asesores: sistema visual, componentes, navegación y comportamiento por vista (#178) |
| [`ESTRUCTURA-DEL-PROYECTO.md`](ESTRUCTURA-DEL-PROYECTO.md) | Árbol de carpetas y qué responsabilidad tiene cada una |

## Para correrlo y probarlo

| Documento | Para qué sirve |
| :--- | :--- |
| [`TESTING.md`](TESTING.md) | Levantar el stack, probar el bot, el panel, Bedrock y las trazas a mano, y correr cada suite de tests |
| [`DEMO.md`](DEMO.md) | Guion de la demo en vivo, qué hace y qué no hace el agente por diseño, plan B y los ensayos medidos |
| [`../harness/README.md`](../harness/README.md) | Formato de los guiones del arnés E2E, la evaluación en vivo y qué valida en cada turno |

El estado de cada historia (To Do / In Progress / Done) vive solo en GitHub Issues y en el
[Project](https://github.com/orgs/softserve-leads-inmobiliaria/projects/1), nunca en estos
archivos.
