# Pruebas y entorno local

## Levantar el stack base

Requiere Docker Desktop (Windows/Mac) o Docker Engine + Compose (Linux) **abierto y corriendo**.

```
docker compose up --build
```

No hace falta crear un `.env`: `docker-compose.yml` trae defaults de desarrollo. Copiar
`.env.example` a `.env` solo si se quiere cambiar algún puerto o credencial.

| Servicio | URL / puerto | Qué es |
| :--- | :--- | :--- |
| `postgres` | `localhost:5432` (usuario/clave/BD: ver `.env.example`) | Postgres 16 con `pgvector` |
| `phoenix` | http://localhost:6006 (OTLP/HTTP `/v1/traces`, OTLP/gRPC en `4317`) | Observabilidad (DT-9) |
| `panel` | http://localhost:8080/health | Panel de asesores (por ahora solo el esqueleto) |
| `migrate` | — (termina al acabar) | Aplica `alembic upgrade head` (esquema completo, C-3) |
| `catalog` | solo dentro de la red de Docker (`http://catalog:8000`, MCP en `/mcp`) | Catálogo *fake* como servidor MCP (CT-8) |
| `core` | http://localhost:8000/health (`POST /events`) | Core *fake* (CT-7 + CT-4) |
| `harness` | — (termina al acabar) | Reproduce `harness/fixtures/*.jsonl` contra el core |

El arnés corre solo al levantar el stack: al final imprime `N/N guiones en verde` y termina, sin
apagar el resto. Para obtener su resultado como código de salida (0 = todo en verde), levantando
solo lo que necesita:

```
docker compose run --rm --build harness
```

Para ver el estado persistido de las conversaciones que dejó el arnés:

```
docker compose exec postgres psql -U leads -d leads -c "select id, fsm_state from conversations"
```

`migrate` aplica las migraciones de Alembic (`db/migrations`) sobre Postgres y termina; los
servicios que usen la base dependerán de él.

Esto es el esqueleto end-to-end de CT-4: arnés → core → catálogo por MCP → respuesta. Todo el
contenido es *fake*, a propósito. Para probar un mensaje a mano con el stack arriba:

```
curl -X POST localhost:8000/events -H "Content-Type: application/json" -d @contracts/examples/event.valid.json
```

Para apagar: `docker compose down`. Para borrar también los datos: `docker compose down -v`.

## Pruebas automáticas

```
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests/contracts       # contratos: no necesitan ningún servicio levantado
python -m pytest tests/infra           # valida docker-compose.yml (sin Docker)
python -m pytest db/tests              # esquema: los de migraciones necesitan Postgres (ver abajo)
python -m pytest services/core/tests
python -m pytest services/catalog/tests
python -m pytest services/panel/tests
python -m pytest tests/harness         # arnés + e2e: levanta catalog y core como procesos, sin Docker
```

### Tests de la base de datos

Los tests de `db/`, `services/core/` y `tests/harness` (estado de la FSM, e2e) usan un Postgres real
mediante `db/testing.py`: una base temporal ya migrada que se borra sola.

`db/tests/test_migrations.py` corre contra un Postgres real: crea una base temporal, aplica
`alembic upgrade head`, prueba constraints y persistencia de la FSM, y la borra. Con Postgres
levantado (`docker compose up -d postgres`) corren solos; sin él se omiten. En CI (`REQUIRE_DB=1`)
nunca se omiten.

Cambiar el esquema: editar `db/models.py` y generar una migración
(`alembic revision --autogenerate -m "descripcion"`, con Postgres arriba); revisarla a mano. El
test `test_migrations_and_models_do_not_drift` falla si los modelos y las migraciones difieren.

Cada suite se corre por separado (los servicios comparten el nombre de paquete `app`); el CI
hace lo mismo en `.github/workflows/ci.yml`.

## Caveats de Docker conocidos

Se agregan aquí apenas se descubren.

- **"failed to connect to the docker API ... dockerDesktopLinuxEngine"** (Windows): Docker Desktop
  no está corriendo. Abrirlo y esperar a que diga "Engine running" antes de `docker compose up`.
- **Puerto ocupado** (p. ej. `5432` si hay un Postgres local, o `8080` si otro programa ya lo usa
  — pasó en Windows con un servicio del sistema): cambiar `POSTGRES_PORT` / `PANEL_PORT` en `.env`,
  o solo para una corrida: `PANEL_PORT=8090 docker compose up`. El error dice "ports are not
  available ... Solo se permite un uso de cada dirección de socket".
- **Docker Desktop no arranca con doble clic** (Windows): probar `docker desktop start` desde la
  terminal, que además espera a que el motor responda.
