##@ Calidad

.PHONY: lint test ci test-db-ready

# Las mismas suites y en el mismo orden que .github/workflows/ci.yml. Cada una en su propio pytest:
# los servicios comparten el nombre de paquete `app`.
SUITES := contracts infra db core catalog panel harness
SUITE_contracts := tests/contracts
SUITE_infra := tests/infra
SUITE_db := db/tests
SUITE_core := services/core/tests
SUITE_catalog := services/catalog/tests
SUITE_panel := services/panel/tests
SUITE_harness := tests/harness
# Las que necesitan Postgres; contracts e infra corren sin Docker.
DB_SUITES := db core catalog panel harness

# Como CI: sin Postgres los tests de base fallan en vez de saltarse. `make test REQUIRE_DB=0` los salta.
REQUIRE_DB ?= 1
# El puerto del .env llega a db/settings.py: si se cambió POSTGRES_PORT, los tests van al mismo Postgres.
PYTEST = REQUIRE_DB=$(REQUIRE_DB) POSTGRES_PORT=$(POSTGRES_PORT) $(PY) -m pytest

# Si ya responde un Postgres donde lo buscan los tests (el del stack, otro clon, uno nativo), se usa ese:
# levantar el del compose chocaría por el puerto. Si no responde ninguno, se levanta.
test-db-ready:
	@POSTGRES_PORT=$(POSTGRES_PORT) $(PY) -c "import psycopg; from db.settings import database_url; \
	psycopg.connect(database_url().replace('+psycopg', ''), connect_timeout=3).close()" 2>/dev/null \
	  && echo "==> Postgres en 127.0.0.1:$(POSTGRES_PORT) ya responde" \
	  || $(MAKE) --no-print-directory db-up

lint: ## ruff check . (lo mismo que CI)
	$(PY) -m ruff check .

test: test-db-ready ## Las 7 suites como CI: sigue aunque una falle y al final informa
	@status=0; \
	for entry in $(foreach suite,$(SUITES),$(suite):$(SUITE_$(suite))); do \
	  suite=$${entry%%:*}; dir=$${entry#*:}; \
	  echo "==> $$suite ($$dir)"; \
	  $(PYTEST) "$$dir" -q $(ARGS) || { status=1; failed="$$failed $$suite"; }; \
	done; \
	[ $$status = 0 ] && echo "==> Todas las suites en verde" || echo "==> Fallaron:$$failed"; \
	exit $$status

test-%: ## Una suite (contracts infra db core catalog panel harness): make test-core ARGS="-k consent"
	@$(if $(SUITE_$*),,echo "Suite desconocida: $* (opciones: $(SUITES))" >&2; exit 1)
	@$(if $(filter $*,$(DB_SUITES)),$(MAKE) --no-print-directory test-db-ready,true)
	$(PYTEST) $(SUITE_$*) $(ARGS)

ci: lint test ## lint + todas las suites, como el workflow de CI
