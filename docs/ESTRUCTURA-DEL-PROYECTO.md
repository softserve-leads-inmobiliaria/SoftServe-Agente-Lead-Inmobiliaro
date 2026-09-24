# Estructura del proyecto (scaffold recomendado)

Este documento define, antes de escribir la primera línea de código de la v2, qué directorios y archivos existen, qué responsabilidad tiene cada uno, y qué convención sigue. El objetivo es que cualquier persona (o agente de IA) que se incorpore sepa, sin preguntar, dónde va cada cosa nueva.

## 0. Principio rector

**Un solo lugar para cada tipo de información, nunca dos.** La v1 terminó con 2 árboles de documentación de arquitectura, 6+ superficies de estado sincronizadas a mano, y un mismo bug copiado 3 veces antes de compartirse en un módulo común (retrospectiva §3). Cada regla de este documento existe para que ese tipo de duplicación sea difícil de cometer por accidente, no solo "recordada".

## 1. Árbol completo

```
SoftServe-Agente-Lead-Inmobiliaro/
├── README.md                    # Quickstart: qué es, docker-compose up, link a docs/README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
├── .dockerignore
├── .github/
│   └── workflows/
│       └── ci.yml               # Único pipeline: lint + tests + contract tests + migraciones
├── contracts/                   # Los 3 contratos congelados (CT-1/2/3) — fuente de verdad de forma de datos
│   ├── event.schema.json
│   ├── fsm_state.schema.json
│   ├── mcp/
│   │   └── *.schema.json        # Un schema por tool catalog.*
│   └── examples/
│       └── *.json               # Ejemplos válidos e inválidos, uno por schema
├── db/                          # Paquete Python compartido — ver §2
│   ├── models.py
│   ├── settings.py
│   ├── logging_config.py
│   ├── repo_root.py             # Resuelto UNA vez, importado por los 3 servicios — ver §2.1
│   ├── migrations/              # Alembic
│   └── tests/
├── services/
│   ├── core/                    # Orquestador + FSM + guardrails + canal Telegram
│   │   ├── app/
│   │   ├── tests/
│   │   └── Dockerfile
│   ├── catalog/                 # Servidor MCP + REST sobre Postgres (tools catalog.*)
│   │   ├── app/
│   │   ├── tests/
│   │   └── Dockerfile
│   └── panel/                   # Panel de asesores (FastAPI + Jinja2)
│       ├── app/
│       ├── tests/
│       └── Dockerfile
├── harness/                     # Arnés de conversaciones E2E (CT-9/OBS-6)
│   ├── fixtures/*.jsonl
│   └── scripts/
│       ├── replay.py
│       └── run_all.py
├── tests/
│   ├── contracts/                # Un test por contrato — corre sin ningún servicio levantado
│   └── harness/
└── docs/                        # ÚNICO árbol de documentación — ver §3
    ├── README.md                 # Índice de navegación (reemplaza "wiki/" de la v1)
    ├── DECISIONS/
    │   └── decisions.md
    ├── DESIGN/
    │   ├── design.md
    │   ├── spec.md
    │   └── guardrails.md
    ├── STORIES/                  # Épicas + historias de usuario, un archivo por historia — ver el manual en docs/STORIES/README.md
    ├── TESTING.md
    ├── DEMO.md
    └── LESSONS-LEARNED.md        # Arranca como copia de la de v1 + un nuevo §0 "heredado de v1"
```

## 2. `db/` — paquete compartido desde el commit 1

En v1, la necesidad de un paquete compartido (`db/`) se descubrió recién en E1/E3, y el patrón de resolución de `sys.path` para importarlo desde cada servicio (`app/_repo_root.py`) se copió a mano 3 veces con el mismo bug (`Path.parents[N]` indexado sin filtrar) antes de extraerse a un helper único (lecciones v1 §2, "Un comentario TODO..."/"Documentar un bug como decisión..."). Para v2:

- `db/` existe desde CT-5 (el primer commit de estructura), no se crea "cuando haga falta".
- `db/repo_root.py` es el ÚNICO lugar que resuelve la ruta del repo entre imagen Docker y checkout local, con índices de `Path.parents` **filtrados antes de indexar** (`[i for i in candidatos if i < len(_HERE.parents)]`), nunca indexados directo. Todo servicio que necesite importar algo de `db.*` o `contracts/` importa desde aquí — nunca reimplementa la resolución de ruta.
- `db/models.py` son los modelos SQLAlchemy — **fuente de verdad única del esquema**, cada servicio los importa, ninguno mantiene su propia copia.
- `db/logging_config.py` centraliza el formato de logging (JSON) **desde el día 1**, PII-masking incluido desde el principio (ver `docs/STORIES/OBS-07-*.md`) — en v1 esto se partió en dos (logs JSON primero, PII después, sin caso real que enmascarar) por construirse tarde; en v2 se diseña completo aunque se implemente en fases.

## 3. `docs/` — un solo árbol, nunca dos

La v1 terminó con `DOCS/` (plan original, cloud, abandonado) y `docs-local/` (plan real) coexistiendo en la raíz del repo, con una nota aclaratoria como único mecanismo para no confundirlas (retrospectiva §3.1). Regla para v2: **si se pivotea de arquitectura, el plan anterior se archiva (`git mv` a una rama o se borra, recuperable del historial) el mismo día del pivote — nunca queda un segundo árbol completo navegable en la raíz.**

- `docs/README.md` es el único índice de navegación (equivalente al `wiki/README.md` de v1, pero sin la ambigüedad de vivir en una carpeta con nombre distinto al árbol que indexa).
- `docs/DECISIONS/decisions.md` sigue existiendo — fue de las cosas que sí funcionó en v1 — pero cambia de rol: es la bitácora técnica de *por qué* (contexto, alternativas, consecuencias), no el lugar donde se repite *qué está hecho*. El estado ("hecho"/"pendiente"/"en curso") vive **solo** en GitHub Issues/Project — ver §5. Cada entrada de `decisions.md` enlaza al Issue/PR correspondiente en vez de reafirmar su estado.
- `docs/DESIGN/guardrails.md` incluye, desde la primera versión, una **tabla de estado por guardrail** (cubierto / parcial / pendiente / fuera de alcance), no solo la lista de qué debería existir. En v1 esta brecha (A-7 cubría 6/8 *claims*, A-8 nunca se construyó) vivía documentada en 2 lugares distintos (`design.md` y `decisions.md`) que había que cruzar para saber el estado real (retrospectiva §3.6).
- `docs/STORIES/` reemplaza `PLAN/tasks.md` (una sola tabla gigante) por un archivo por historia — ver el manual completo en `docs/STORIES/README.md`.

## 4. `services/*` — convención por servicio

Cada servicio (`core`, `catalog`, `panel`) es independiente en Docker/despliegue pero comparte `db/` y `contracts/` vía el `build.context` apuntando a la raíz del repo (patrón que ya funcionó en v1, DT-17). Dentro de cada `app/`:

- `main.py` — arranque FastAPI, `lifespan`, configuración de logging (`db.logging_config.configure_json_logging()` desde la primera línea después de los imports).
- Un módulo por responsabilidad (p. ej. `fsm.py`, `orchestrator.py`, `guardrails.py`, `scoring.py` en `core`) — nunca un solo archivo con toda la lógica.
- `tests/` espeja la estructura de `app/`.

**Decisión de arquitectura que v1 no tomó desde el día 1** (retrospectiva §3.3/§4.1.1): el estado de conversación (`FsmState`, historial, score) se lee y escribe **directo en Postgres** desde la primera historia que lo necesita (equivalente a A-1 en v1). No existe una versión "en memoria" que se persista después — evita la clase entera de historias tipo "Gap: falta persistir" y el riesgo de que memoria y Postgres diverjan.

## 5. Gestión de estado del backlog — una sola fuente de verdad

GitHub Issues + Project v2 es el **único** lugar donde vive el estado (To Do / In Progress / Done) de cualquier historia. `docs/STORIES/<ID>.md` es el contenido versionado de la historia (criterios de aceptación, Definition of Done) — no repite ni cachea su propio estado; cada archivo enlaza al Issue correspondiente. Esto evita el problema de v1 de 4+ auditorías de "drift" manuales entre Issues, Project, `todo.md` y `decisions.md` (retrospectiva §3.2). Automatizar desde el Sprint 1 el alta al Project al crear el Issue (un `workflow` simple en `on: issues: [opened]`) en vez de tratarlo como un paso manual aparte.

## 6. Qué NO crear (por ahora)

- **No** una segunda carpeta de documentación "por si acaso" (ver §3).
- **No** un archivo de estado paralelo (`todo.md` de v1 cumplió una función real de "trámites/decisiones abiertas del equipo" — si se recrea en v2, que sea *solo* eso: pendientes no técnicos, nunca una tabla de historias cerradas que duplique el Project).
- **No** lógica de un servicio copiada a otro "por ahora, ya se comparte después" — si dos servicios la necesitan, va a `db/` (o a un futuro `libs/` si crece más allá de modelos/config) desde la primera vez que se repite.
