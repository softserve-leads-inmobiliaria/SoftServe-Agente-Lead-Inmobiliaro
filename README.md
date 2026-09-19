# Agente de Leads Inmobiliarios

Agente conversacional por Telegram que califica leads inmobiliarios (BANT-E), recomienda
propiedades con datos reales del catálogo y pasa el lead a un asesor humano.

## Documentación

**Empezá por [`docs/README.md`](docs/README.md)** — es el único índice de la documentación
(arquitectura, decisiones, historias de usuario, guía de onboarding).

Reglas de trabajo del equipo: [`CONTRIBUTING.md`](CONTRIBUTING.md). Primera vez con Git/GitHub en
equipo: [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Estructura

| Carpeta | Qué contiene |
| :--- | :--- |
| `contracts/` | Contratos congelados (evento canónico, estado de la FSM, tools MCP) y sus ejemplos |
| `db/` | Paquete Python compartido: modelos, configuración, logging, resolución de la raíz del repo |
| `services/core/` | Orquestador, FSM, guardrails, scoring y canal Telegram |
| `services/catalog/` | Catalog Service (MCP + REST sobre Postgres) |
| `services/panel/` | Panel interno de asesores |
| `harness/` | Arnés de conversaciones E2E |
| `tests/` | Contract tests y pruebas del arnés |
| `docs/` | Único árbol de documentación |

## Correr los tests

```
python -m venv .venv
.venv/Scripts/activate        # Windows (Git Bash); en Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests/contracts
python -m pytest db/tests
python -m pytest services/core/tests
```

CI (`.github/workflows/ci.yml`) corre lo mismo en cada Pull Request.
