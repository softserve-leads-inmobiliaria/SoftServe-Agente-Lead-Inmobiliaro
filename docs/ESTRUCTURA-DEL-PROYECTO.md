# Estructura del proyecto

Qué directorios y archivos existen, qué responsabilidad tiene cada uno y qué convención siguen. El
objetivo es que cualquier persona (o agente de IA) que se incorpore sepa, sin preguntar, dónde va cada
cosa nueva.

## 0. Principio rector

**Un solo lugar para cada tipo de información, nunca dos.** Dos árboles de documentación, varias
superficies de estado sincronizadas a mano o la misma lógica copiada en tres servicios terminan
divergiendo sin que nadie lo note. Cada regla de este documento existe para que ese tipo de duplicación
sea difícil de cometer por accidente, no solo "recordada".

## 1. Árbol

```
SoftServe-Agente-Lead-Inmobiliaro/
├── README.md                    # Qué es, dónde empezar a leer, cómo correr los tests
├── CONTRIBUTING.md              # Reglas de trabajo del equipo
├── docker-compose.yml           # Stack completo: postgres, phoenix, migrate, seed, catalog, core, panel, harness
├── .env.example                 # Variables de entorno con sus defaults de desarrollo
├── .dockerignore / .gitignore
├── pyproject.toml               # Configuración de ruff y pytest
├── requirements-dev.txt         # Todo lo necesario para correr las suites en local y en CI
├── alembic.ini
├── .github/
│   ├── workflows/
│   │   ├── ci.yml               # Único pipeline: ruff + las 7 suites de tests
│   │   └── add-to-project.yml   # Alta automática de cada Issue nuevo al Project (§5)
│   ├── ISSUE_TEMPLATE/          # Historia de usuario / gap, bug o deuda técnica
│   ├── PULL_REQUEST_TEMPLATE.md # Checklist de Definition of Done
│   └── CODEOWNERS
├── contracts/                   # Contratos congelados (CT-1/2/3): fuente de verdad de la forma de los datos
│   ├── event.schema.json
│   ├── fsm_state.schema.json
│   ├── mcp/*.schema.json        # Un schema por tool del catálogo
│   ├── scheduling/*.schema.json # Agenda por HTTP (C-2): fuera de mcp/ para que el LLM no la vea
│   └── examples/*.json          # Ejemplos válidos e inválidos de cada schema
├── db/                          # Paquete Python compartido por los 3 servicios (§2)
│   ├── models.py                # Modelos SQLAlchemy: única fuente de verdad del esquema
│   ├── session.py / settings.py / logging_config.py / repo_root.py
│   ├── testing.py               # Base temporal ya migrada para los tests
│   ├── migrations/              # Alembic
│   ├── tests/
│   └── Dockerfile               # Imagen de los servicios migrate y seed
├── services/
│   ├── core/                    # FSM, agente, guardrails, score y canal de Telegram
│   │   ├── app/                 # channels/ (Telegram), fakes/ (agente sin LLM), un módulo por responsabilidad
│   │   ├── tests/
│   │   └── Dockerfile
│   ├── catalog/                 # Catalog Service: servidor MCP y agenda por HTTP sobre Postgres
│   │   ├── app/
│   │   ├── tests/
│   │   └── Dockerfile
│   └── panel/                   # Panel de asesores (FastAPI + Jinja2)
│       ├── app/
│       ├── tests/
│       └── Dockerfile
├── harness/                     # Arnés de conversaciones E2E contra el stack en Docker
│   ├── fixtures/*.jsonl         # Un guion por archivo
│   ├── scripts/                 # replay.py, run_all.py
│   └── README.md                # Formato de los guiones
├── scripts/
│   ├── seed_advisors.py         # Asesores de demo (servicio seed)
│   └── github/                  # Labels, Issues desde las historias, Project, protección de main
├── tests/
│   ├── contracts/               # Un test por contrato; corre sin ningún servicio levantado
│   ├── harness/                 # Arnés y E2E levantando catalog y core como procesos
│   └── infra/                   # Compose, Dockerfiles y enlaces de la documentación, sin Docker
└── docs/                        # Único árbol de documentación (§3)
    ├── README.md                # Índice de navegación
    ├── ONBOARDING.md            # Tutorial de Git/GitHub para el equipo
    ├── ESTRUCTURA-DEL-PROYECTO.md
    ├── TESTING.md               # Levantar el stack, probar a mano, correr cada suite
    ├── DECISIONS/decisions.md   # Bitácora de decisiones de arquitectura (DT-*)
    ├── DESIGN/
    │   ├── design.md            # Referencia compartida y estado real del sistema (§5)
    │   └── guardrails.md        # Guardrails de entrada y salida, y su cobertura
    ├── STORIES/                 # Épicas, roadmap e historias de usuario, un archivo por historia
    └── presentations/           # Presentaciones de avance, una por fecha
```

## 2. `db/` — paquete compartido

- `db/` existe desde CT-5 (el primer commit de estructura): no se crea "cuando haga falta".
- `db/repo_root.py` es el **único** lugar que resuelve la ruta del repo entre la imagen Docker y el
  checkout local, con los índices de `Path.parents` **filtrados antes de indexar**
  (`[i for i in candidatos if i < len(_HERE.parents)]`), nunca indexados directo. Todo servicio que
  necesite importar algo de `db.*` o `contracts/` lo hace a través de él y nunca reimplementa la
  resolución de ruta.
- `db/models.py` son los modelos SQLAlchemy: **fuente de verdad única del esquema**. Cada servicio los
  importa y ninguno mantiene su propia copia. `test_migrations_and_models_do_not_drift` falla si modelos
  y migraciones difieren.
- `db/logging_config.py` centraliza el formato de logging (JSON) en los tres servicios. La decisión
  sobre PII en los logs está en DT-27.

## 3. `docs/` — un solo árbol, nunca dos

**Si se pivotea de arquitectura, el plan anterior se borra (queda recuperable en el historial) el mismo
día del pivote**: nunca queda un segundo árbol completo navegable.

- `docs/README.md` es el único índice de navegación. `tests/infra/test_docs_links.py` exige que todo
  `.md` del repo se alcance desde el README raíz y que ningún enlace relativo esté roto.
- `docs/DECISIONS/decisions.md` es la bitácora técnica del *por qué* (contexto, alternativas,
  consecuencias), no el lugar donde se repite *qué está hecho*. Solo se amplía: una decisión que cambia
  lleva una entrada nueva que referencia la anterior. Cada entrada enlaza al Issue o PR que la originó.
- `docs/DESIGN/guardrails.md` incluye una **tabla de estado por guardrail** (implementado / parcial /
  pendiente), no solo la lista de lo que debería existir.
- `docs/DESIGN/design.md` §5 es la foto de qué está construido de verdad, separada del plan de las
  secciones 1-4.
- `docs/STORIES/` tiene un archivo por historia. El manual está en `docs/STORIES/README.md`.

## 4. `services/*` — convención por servicio

Cada servicio (`core`, `catalog`, `panel`) es independiente en Docker pero comparte `db/` y
`contracts/` porque su `build.context` apunta a la raíz del repo. Dentro de cada `app/`:

- `main.py`: arranque de FastAPI, `lifespan` y logging (`db.logging_config.configure_json_logging()`
  desde la primera línea después de los imports).
- Un módulo por responsabilidad (en `core`: `fsm.py`, `turn.py`, `agent.py`, `input_guard.py`,
  `scoring.py`…), nunca un solo archivo con toda la lógica.
- `tests/` espeja la estructura de `app/`.
- Todos los servicios comparten el nombre de paquete `app`, así que cada suite se corre en su propio
  comando (ver `docs/TESTING.md`).

**Decisión de arquitectura** (DT-1): el estado de la conversación (`FsmState`, historial, score) se lee
y escribe **directo en Postgres** desde la primera historia que lo necesita. No existe una versión en
memoria que se persista después, así que memoria y base no pueden divergir.

## 5. Gestión de estado del backlog — una sola fuente de verdad

GitHub Issues + Project es el **único** lugar donde vive el estado (To Do / In Progress / Done) de
cualquier historia. `docs/STORIES/<ID>.md` es el contenido versionado de la historia (criterios de
aceptación, Definition of Done): no repite ni cachea su propio estado. Así no hace falta auditar a mano
que Issues, Project y documentos digan lo mismo.

Cada Issue nuevo entra solo al Project mediante `.github/workflows/add-to-project.yml`. Ese *workflow*
necesita un token con acceso al Project; mientras no lo tenga, el alta se hace con `gh project item-add`.

## 6. Qué NO crear

- **No** una segunda carpeta de documentación "por si acaso" (ver §3).
- **No** un archivo de estado paralelo. Si hace falta anotar pendientes no técnicos del equipo
  (trámites, decisiones abiertas), que sea solo eso, nunca una tabla de historias cerradas que duplique
  el Project.
- **No** lógica de un servicio copiada a otro "por ahora, ya se comparte después": si dos servicios la
  necesitan, va a `db/` (o a un futuro `libs/` si crece más allá de modelos y configuración) desde la
  primera vez que se repite.
