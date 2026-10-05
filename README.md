# Agente de Leads Inmobiliarios

Agente conversacional por Telegram que califica leads inmobiliarios (BANT-E), recomienda
propiedades con datos reales del catálogo y pasa el lead a un asesor humano.

## Documentación

**Empezá por [`docs/README.md`](docs/README.md)** — es el único índice de la documentación
(arquitectura, decisiones, historias de usuario, guía de onboarding). Qué está construido de verdad
y qué no: [`docs/DESIGN/design.md`](docs/DESIGN/design.md) §5. Cómo levantarlo y probarlo:
[`docs/TESTING.md`](docs/TESTING.md).

Reglas de trabajo del equipo: [`CONTRIBUTING.md`](CONTRIBUTING.md). Primera vez con Git/GitHub en
equipo: [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Estructura

| Carpeta | Qué contiene |
| :--- | :--- |
| `contracts/` | Contratos congelados (evento canónico, estado de la FSM, tools MCP, agenda) y sus ejemplos |
| `db/` | Paquete Python compartido: modelos, configuración, logging, resolución de la raíz del repo |
| `services/core/` | Orquestador, FSM, guardrails, scoring y canal Telegram |
| `services/catalog/` | Catalog Service (MCP + REST sobre Postgres) |
| `services/panel/` | Panel interno de asesores |
| `harness/` | Arnés de conversaciones E2E |
| `tests/` | Contract tests, pruebas del arnés E2E y validación del compose y los Dockerfiles |
| `docs/` | Único árbol de documentación |

## Comandos rápidos con make

El `Makefile` de la raíz (módulos en `make/`) junta los comandos de este README, de
[`docs/TESTING.md`](docs/TESTING.md) y de CI. Es opcional: los comandos de `docker compose` siguen
valiendo igual. Necesita `make`, que viene en macOS y Linux. En Windows no hace falta instalarlo: Git Bash
no lo trae, y `bash scripts/demo/up.sh` / `down.sh` y los comandos de [`docs/TESTING.md`](docs/TESTING.md)
hacen lo mismo.

```
make help          # todos los objetivos, por sección
make start         # stack (core, panel, phoenix) + asesores, catálogo e historia N-9 + URLs
make install       # .venv (Python 3.12+) con las dependencias de desarrollo
make test          # las 7 suites como CI; usa el Postgres que ya responda o levanta el del compose
```

| Para | Objetivos |
| :--- | :--- |
| Stack | `up`, `down` / `stop`, `clean` (borra volúmenes), `reset`, `rebuild s=core`, `logs s=core`, `ps`, `shell s=core`, `info` |
| Base de datos | `db-up`, `migrate`, `seed`, `seed-history`, `seed-history-clear`, `seed-all`, `db-reset`, `psql`, `migration m="..."` |
| Calidad | `lint`, `test`, `test-<suite>` (`contracts infra db core catalog panel harness`), `ci` |
| Demo y arnés | `demo`, `demo-down`, `demo-purge`, `harness`, `live-run`, `conversar`, `exportar`, `smoke` |

Los argumentos extra van en `ARGS`: `make test-core ARGS="-k consent"`,
`make seed-history ARGS="--hoy 2026-10-01"`, `make live-run ARGS="--sin-frases"`. Los puertos salen del
`.env`. `make harness` corre en un stack aparte y efímero (sin bot, agente *fake*), así que no toca el
stack ni la cola del panel.

## Correr los tests

```
python -m venv .venv
.venv/Scripts/activate        # Windows (Git Bash); en Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests/contracts
```

Son 7 suites y cada una va en su propio comando, porque los servicios comparten el nombre de paquete
`app`. La lista completa y qué necesita cada una (varias piden Postgres) están en
[`docs/TESTING.md`](docs/TESTING.md#pruebas-automáticas). CI (`.github/workflows/ci.yml`) corre las
mismas suites en cada Pull Request.

Para levantar el stack completo y reproducir los guiones E2E contra los contenedores:

```
docker compose run --rm --build harness
```
