# Roadmap — secuenciación con 3 personas en paralelo

> **Plan de partida.** Los lotes, semanas y horas de esta página son la estimación inicial del equipo y no se actualizan con el avance: el estado real está en el [Project](https://github.com/orgs/softserve-leads-inmobiliaria/projects/1) y qué está construido, en [`design.md` §5](../DESIGN/design.md#5-estado-real-del-sistema). Reparto real: Persona 1 = `dbarretol` (track A), Persona 2 = `SantiagoMartinez22` (track B), Persona 3 = `miguelvelezsk` (track C).

Este documento responde dos preguntas que `README.md` (contenido y prioridad de cada historia) y GitHub Issues/Project (estado) no responden: **en qué orden conviene avanzar las historias** y **quién debería tomar cada una** para que 3 personas trabajando a la vez choquen lo menos posible — tanto por *conflictos de merge* (dos personas tocando el mismo archivo) como por *tiempo bloqueado* (esperando una dependencia).

No repite estado (eso vive solo en GitHub Issues/Project — ver `docs/ESTRUCTURA-DEL-PROYECTO.md` §5) ni las historias en sí (eso vive en `README.md` y en cada `<ID>.md`). Si el plan cambia de forma real, este archivo se edita vía PR como cualquier otro doc versionado — no es un tracker que haya que mantener sincronizado a mano turno a turno.

**Supuesto de capacidad usado para todo lo que sigue:** 8 semanas, 3 personas, ~8-10 h/semana cada una (part-time). Si esto cambia, los números de esta página hay que recalcularlos — no son válidos para otra dedicación.

## 1. Primero lo importante: el backlog completo no entra en 8 semanas a este ritmo

Con la fórmula de `README.md` (`horas = puntos × 2`):

| Alcance | Puntos | Horas ideales | Cabe en la capacidad del equipo? |
| :--- | ---: | ---: | :--- |
| Backlog completo (54 historias) | 164 | 328h | No. Capacidad bruta 8 sem × 3 × 9h ≈ 216h; neta (~20% para PRs/standups/integración) ≈ 173h |
| Solo P0 (42 historias) | 137 | 274h | No — sigue por encima de la capacidad neta |
| **Piso absoluto real** (ver §2) | **86** | **172h** | **Prácticamente exacto** — llena casi toda la capacidad neta de las semanas 1-7, dejando la semana 8 para `Z-*` (hardening/demo) |

**Conclusión práctica:** a esta dedicación, el "piso absoluto" que ya define `EPICAS.md` no es una lista de emergencia — es, con los números reales, el alcance que el equipo puede completar con margen de maniobra cercano a cero. Todo lo que sea P0-pero-no-piso es alcance que compite por el mismo tiempo, y P1/P2 son estrictamente oportunistas (solo si la velocidad real termina siendo mejor que la estimación, o si se reduce algo del resto de P0).

Esto no es una recomendación de recortar alcance: la prioridad y el "piso absoluto" ya los definió el equipo en `README.md`/`EPICAS.md`. Es la traducción a horas de esa decisión, para que quede explícito *antes* de la semana 6 y no se descubra en la semana 6.

## 2. Hallazgo: el piso absoluto listado en `EPICAS.md` tiene dos dependencias implícitas sin listar

La lista de `EPICAS.md`/`README.md` (`CT-1..CT-9, A-1, A-4, A-5, A-6, A-9, A-12, B-1, B-5, B-7, C-1, C-1a, C-1b, C-4, C-7, C-9`) no incluye:

- **`A-3`** (Cliente MCP para tools de catálogo, 3 pts) — pero `A-4` depende explícitamente de `A-3` en la tabla de `README.md`. Sin `A-3`, `A-4` no se puede construir.
- **`C-3`** (Esquema de datos completo con migraciones, 5 pts) — pero tanto `B-5` como `C-9` (ambas en el piso absoluto) dependen de `C-3`.

El piso absoluto *efectivo* (el mínimo que realmente se puede construir de punta a punta) son 26 historias / 86 puntos, no 24/78. `EPICAS.md` ya incluye A-3 y C-3 en la lista.

## 3. Asignación por persona — ownership de archivos, no solo de historias

La forma más efectiva de evitar conflictos de merge con 3 personas no es "quién hace qué historia" sino "quién es dueño de qué carpeta", porque los conflictos reales pasan por editar el mismo archivo a la vez, no por trabajar en historias relacionadas. Asignación recomendada (usar `Persona 1/2/3` como placeholder — reemplazar por nombres reales al confirmar):

| Persona | Track | Dueño de | Historias principales |
| :--- | :--- | :--- | :--- |
| **Persona 1** | `A` — motor del agente | `services/core/app/{fsm,orchestrator,guardrails,scoring}.py` | A-1..A-13, OBS-1..3 |
| **Persona 2** | `B` — canal y borde | `services/core/app/<canal telegram>`, `services/panel/` | B-1..B-9, OBS-6, OBS-7 |
| **Persona 3** | `C` — datos y conocimiento | `services/catalog/`, `db/` | C-1..C-9, OBS-4, OBS-5 |

Puntos de fricción conocidos que NO se resuelven con ownership por track (archivos genuinamente compartidos) — tratarlos como *sync points* cortos (par/mob de 15-30 min), no como trabajo paralelo solitario:

- `contracts/*.schema.json` (CT-1/2/3) — cada persona define el contrato que va a consumir (ver §4), pero el archivo `contracts/examples/` conviene revisarlo entre los tres antes de congelarlo.
- `db/models.py`, `db/repo_root.py`, `db/logging_config.py` (CT-5, C-3) — un solo dueño (Persona 3) todo el proyecto; los otros dos consumen, no editan.
- `docker-compose.yml`, `.github/workflows/ci.yml` (CT-5, CT-6) — cambios chicos y poco frecuentes; avisar en el canal del equipo antes de tocarlos, no por PR silencioso.
- `docs/STORIES/README.md` — solo se edita al agregar una historia nueva (raro); nunca para marcar estado.
- `docs/DECISIONS/decisions.md` — apend-only por diseño (cada entrada es un bloque nuevo), bajo riesgo de conflicto real aunque lo toquen varias personas.

## 4. Orden recomendado (lotes que respetan dependencias)

Cada lote agrupa historias que ya tienen sus dependencias resueltas por el lote anterior y que las 3 personas pueden tomar en simultáneo sin bloquearse. Dentro de un lote, el orden entre personas es indistinto.

### Semana 1 — Lote 1: contratos y fundamentos (sin dependencias)

| Quién | Historia | Motivo del emparejamiento |
| :--- | :--- | :--- |
| Persona 1 | `CT-3` (FSM state) | Es quien más la va a consumir en `A-1` |
| Persona 1 | `C-4` (reglas BANT-E) | Sin dependencias, lógica de negocio pura, cabe en el hueco |
| Persona 2 | `CT-1` (evento canónico) | Es quien más lo va a consumir en `B-3` |
| Persona 2 | `CT-5` (repo/CI) | Setup de infra, bajo riesgo de choque en semana 1 |
| Persona 3 | `CT-2` (contrato MCP catalog) | Es quien más lo va a consumir en `C-1` |
| Persona 3 | `CT-6` (docker-compose) | Dueña de `db/`, natural que arranque la infra compartida |

### Semana 1-2 — Lote 2: desbloqueados por el Lote 1

| Quién | Historia | Depende de (ya resuelto) |
| :--- | :--- | :--- |
| Persona 1 | `A-9` | `C-4` |
| Persona 2 | `CT-7`, `CT-9` | `CT-1` |
| Persona 3 | `CT-8`, `C-1`, `C-3` | `CT-2` / `CT-3` |
| **Los 3** | `CT-4` (integración, 5 pts) — sesión conjunta corta | `CT-1, CT-2, CT-3, CT-6` |

`CT-4` es el único punto donde recomiendo trabajo conjunto en vez de paralelo: es la integración de los 3 *fakes*, y hacerla en solitario por una sola persona reintroduce el riesgo que `EPICAS.md` dice que E0 existe para evitar (negociar formatos a mitad de camino).

### Semana 2-3 — Lote 3

| Quién | Historia | Depende de |
| :--- | :--- | :--- |
| Persona 1 | `A-1` | `CT-4` |
| Persona 2 | `B-1` | `CT-4` |
| Persona 3 | `A-3`, `C-1a` | `CT-8` / `C-1` |

### Semana 3-4 — Lote 4

| Quién | Historia | Depende de |
| :--- | :--- | :--- |
| Persona 1 | `A-4` | `A-3`, `C-1` |
| Persona 2 | `B-5` | `C-3` |
| Persona 3 | `C-1b`, `C-7`, `C-9` | `C-1`, `C-1a`, `C-3` |

### Semana 4-5 — Lote 5

| Quién | Historia | Depende de |
| :--- | :--- | :--- |
| Persona 1 | `A-5` | `A-4` |
| Persona 2 | `B-7` | `B-5` |
| Persona 3 | *(adelanta P0 restante — ver §5)* | — |

### Semana 5 — Lote 6 (cierra el piso absoluto efectivo)

| Quién | Historia | Depende de |
| :--- | :--- | :--- |
| Persona 1 | `A-6` | `A-5` |
| Persona 1/2 | `A-12` | `A-9`, `B-7` |

Con esto, las 26 historias del piso absoluto efectivo (86 pts / 172h) quedan cubiertas en ~5 semanas si la velocidad real se acerca a la estimada — dejando 2 semanas de colchón antes de la semana 8 (`Z-*`) para el resto de P0.

## 5. Después del piso absoluto: qué tomar si hay tiempo (semanas 5-7)

En orden de prioridad, sin pelearse por archivos (ya asignados por track en §3):

1. **Resto de P0** en el orden de dependencias de cada track: Track A → `A-2, A-7, A-11` · Track B → `B-2, B-3, B-4` · Track C → `C-2*, C-6, C-8` (*`C-2` es P1, no P0 — solo si sobra tiempo, ver siguiente punto*) · Cross → `OBS-1..5`.
2. **P1 solo si el equipo va adelantado** respecto al plan de §4 — no por defecto. Candidatos en orden de valor/costo: `A-8` (guardrail de entrada, barato, 4h) y `OBS-7` (health+logs, barato, 4h) primero; `B-6`, `B-8`, `B-9`, `C-2`, `C-5`, `A-10`, `A-13` después.
3. **P2 (`OBS-8`) por defecto fuera de alcance** — es la única historia P2 del backlog; solo entra si todo lo anterior ya está en verde con margen real.

## 6. Semana 8 — reservada, no negociable

`Z-1` (hardening, 10h), `Z-2` (demo, 6h), `Z-3` (arquitectura final, 6h — sin dependencias, se puede escribir en paralelo desde antes si alguien tiene un hueco) y `Z-4` (docs publicadas, 2h) quedan fuera del cálculo de §1 a propósito: son cierre, no construcción, y compiten por la misma semana entre las 3 personas en vez de repartirse por track.

## 7. Recalibrar, no solo seguir el plan

Estos números son una estimación de partida, no un compromiso. Después de cerrar el Lote 1 y el Lote 2 (fin de semana 2), comparar horas reales vs. estimadas por historia y ajustar §4/§5 en un PR — si la velocidad real es distinta a "2h ideales por punto", toda la fila de abajo de §1 cambia y vale más saberlo en la semana 2 que en la semana 7.

## 8. Propuestas de octubre: E6 y lo que sale de `propuestas.md`

Historias nuevas a partir de [`docs/DESIGN/propuestas.md`](../DESIGN/propuestas.md): N-1 a N-10 (E6), A-15 a A-18 y B-10 a B-14. Las P1 suman 29 puntos (~58 h) y las P2, 19 (~38 h). Con lo pendiente del backlog (B-8, OBS-6, C-5, #104 y #119, ~26 h) y unas 86 h netas hasta la semana 8 (`propuestas.md` §8), **caben las P1, sin margen; las P2 quedan fuera salvo que sobre tiempo**.

### Lotes

Igual que en §4: cada lote solo depende de los anteriores, y dentro de un lote nadie espera a nadie.

| Lote | Historias | Qué desbloquea |
| :--- | :--- | :--- |
| **7: base** | N-1, N-2, A-15 y B-8 (ya en el backlog) | N-1 es el esquema de todo lo demás: su primer PR es solo la migración. N-2 es una decisión de equipo, no código |
| **8: sobre el esquema** | N-3, N-4, N-5, N-6, N-7, N-9, B-10 | Todo depende solo de N-1 (N-7 también de N-2). N-6 y N-7 fijan en su primer PR las firmas que usa N-8 |
| **9: integración** | N-8, B-11 | La vista de gerencia sobre N-6 y N-7; los botones sobre B-8 |
| Backlog | A-16, A-17, A-18, B-12, B-13, B-14, N-10 | P2. Todo lo que necesita el *scheduler* (A-17) cuelga de él |

Después del lote 9 siguen `OBS-6` (una corrida real antes de la semana 8) y la semana 8 de §6. `Z-1` no admite alcance nuevo: lo que no esté en `main` antes de `Z-1` queda fuera.

### Por persona

Mismo reparto por carpetas que §3, para que nadie edite el archivo de otro:

| Persona | Lote 7 | Lote 8 | Lote 9 | Puntos P1 |
| :--- | :--- | :--- | :--- | :---: |
| Track A (`services/core/`) | A-15; conduce N-2 | N-4 → N-5 (mismos archivos, en ese orden), N-7 | — | 9 |
| Track B (canal y panel) | B-8 | N-3, B-10 | N-8, B-11 | 14 |
| Track C (`db/`, `scripts/`, catálogo) | N-1 | N-6, N-9 | — | 9 |

Archivos que se cruzan y cómo se evitan los choques:

- **Migraciones.** Solo N-1 crea una en los lotes 7 a 9. Dos migraciones en paralelo dejan dos *heads* en Alembic.
- **`services/panel/`.** N-6 (`inventory.py`) y N-7 (`attribution.py`) son archivos nuevos. El track B solo toca rutas y plantillas (N-3, N-8).
- **`turn.py` y `state_store.py`.** N-4, N-5 y A-15 son del track A. B-8 también toca `core` para llamar a `get_slots` y `book`: coordinar con el track A o pasársela (ver abajo).
- **`contracts/` y `seed_catalog.py`.** B-10 agrega `photo_url`: avisar al track C antes, como pide §3.

**El cuello de botella es el track B** (14 puntos frente a 9). La forma más limpia de equilibrarlo es que el track A tome B-8: el trabajo es que `core` llame a `get_slots` y `book` (`design.md` §5.3), o sea, la carpeta del track A. Si no alcanza el tiempo, B-11 y luego B-10 son las primeras P1 en salir; N-8 no, porque es lo que pidió el profesor.
