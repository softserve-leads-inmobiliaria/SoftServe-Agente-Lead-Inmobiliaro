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

Para apagar: `docker compose down`. Para borrar también los datos: `docker compose down -v`.

## Pruebas automáticas

```
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests/contracts       # contratos: no necesitan ningún servicio levantado
python -m pytest tests/infra           # valida docker-compose.yml (sin Docker)
python -m pytest db/tests
python -m pytest services/core/tests
python -m pytest services/panel/tests
```

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
