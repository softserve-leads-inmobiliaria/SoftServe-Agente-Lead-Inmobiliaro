##@ Base de datos

.PHONY: db-up migrate seed seed-faq seed-history seed-history-clear seed-all db-reset psql migration

db-up: docker-ready ## Solo postgres (lo que piden los tests)
	$(COMPOSE) up -d --wait postgres

migrate: docker-ready ## Aplica las migraciones (servicio migrate: alembic upgrade head)
	$(COMPOSE) run --rm --build migrate

seed: docker-ready ## Asesores, propiedades y promociones de demo (idempotente)
	$(COMPOSE) run --rm --build seed

seed-faq: docker-ready ## FAQ con embeddings en pgvector (C-5, idempotente)
	$(COMPOSE) run --rm --build catalog python scripts/seed_faq.py

seed-history: docker-ready ## Historia sintética de seis meses (N-9): make seed-history ARGS="--hoy 2026-10-01"
	$(COMPOSE) run --rm seed python scripts/seed_history.py $(ARGS)

seed-history-clear: docker-ready ## Borra la historia sintética
	$(COMPOSE) run --rm seed python scripts/seed_history.py --borrar

seed-all: seed seed-faq seed-history ## seed + seed-faq + seed-history

db-reset: docker-ready ## Base vacía, migrada y sembrada (no toca las trazas de phoenix)
	$(COMPOSE) down --remove-orphans
	docker volume rm -f $(COMPOSE_PROJECT)_pgdata
	$(MAKE) --no-print-directory up seed-all

psql: ## Consola psql en la base del stack
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

migration: db-up ## Nueva migración autogenerada: make migration m="agrega tabla x"
	@[ -n "$(m)" ] || { echo 'Falta la descripción: make migration m="..."' >&2; exit 1; }
	$(PY) -m alembic revision --autogenerate -m "$(m)"
