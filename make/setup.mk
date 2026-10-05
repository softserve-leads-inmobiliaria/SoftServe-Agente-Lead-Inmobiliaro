##@ Entorno local

.PHONY: venv install env doctor docker-ready

# CI y las imágenes usan 3.12; con uno más viejo (el /usr/bin/python3 de macOS es 3.9) la instalación
# falla a mitad de camino.
venv: ## Crea .venv si no existe (Python 3.12+; otro: make venv SYS_PYTHON=python3.12)
	@if [ -d .venv ]; then echo "==> .venv ya existe"; exit 0; fi; \
	$(SYS_PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null || { \
	  echo "ERROR: hace falta Python 3.12 o mayor y $(SYS_PYTHON) es $$($(SYS_PYTHON) --version 2>&1)." >&2; \
	  echo "       Indicar otro: make venv SYS_PYTHON=python3.12" >&2; exit 1; }; \
	echo "==> Creando .venv con $$($(SYS_PYTHON) --version)"; \
	$(SYS_PYTHON) -m venv .venv

install: venv ## Instala las dependencias de desarrollo (db, core, catalog, panel, pytest, ruff)
	$(VENV_PY) -m pip install -r requirements-dev.txt

env: .env ## Crea .env desde .env.example (nunca lo sobrescribe)

# Sin prerrequisitos a propósito: si .env.example cambia, el .env con secretos no se pisa.
.env:
	@cp .env.example .env
	@echo "==> .env creado desde .env.example (todo tiene default; los secretos van aquí)"

doctor: ## Revisa que estén Docker, Compose, el venv y el .env
	@ok=1; \
	command -v docker >/dev/null 2>&1 && echo "  ok    docker" || { echo "  FALTA docker"; ok=0; }; \
	$(COMPOSE) version >/dev/null 2>&1 && echo "  ok    $(COMPOSE)" || { echo "  FALTA $(COMPOSE)"; ok=0; }; \
	docker info >/dev/null 2>&1 && echo "  ok    daemon de Docker" || { echo "  FALTA el daemon de Docker (abrir Docker Desktop)"; ok=0; }; \
	[ -x "$(VENV_PY)" ] || [ -x "$(VENV_PY).exe" ] && echo "  ok    .venv ($$($(VENV_PY) --version 2>&1))" || echo "  aviso sin .venv: make install (solo hace falta para tests y lint)"; \
	[ -f .env ] && echo "  ok    .env" || echo "  aviso sin .env: make env (opcional, todo tiene default)"; \
	[ $$ok = 1 ]

# Como scripts/demo/up.sh: si el daemon no responde, intenta abrir Docker Desktop.
docker-ready:
	@docker info >/dev/null 2>&1 || { \
	  echo "==> El daemon de Docker no responde: docker desktop start"; \
	  docker desktop start --timeout 180 >/dev/null && docker info >/dev/null 2>&1; \
	} || { echo "ERROR: Docker no está corriendo" >&2; exit 1; }
