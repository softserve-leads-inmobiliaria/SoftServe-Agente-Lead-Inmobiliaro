##@ Demo y arnés

.PHONY: demo demo-down demo-purge harness live-run conversar exportar smoke

demo: ## Demo en vivo: stack + seeds + comprobaciones + enlaces (scripts/demo/up.sh)
	bash scripts/demo/up.sh

demo-down: ## Exporta las conversaciones y apaga la demo (scripts/demo/down.sh)
	bash scripts/demo/down.sh

demo-purge: ## Como demo-down y además borra las imágenes
	bash scripts/demo/down.sh --purge

# El arnés corre en su propio proyecto, como en CI: sin bot, con el agente fake y puertos al azar, y se
# borra al terminar. En el proyecto del stack, `run` recrearía el core con este entorno (le quitaría el
# bot a la demo) y los leads de los guiones quedarían en la cola del panel.
HARNESS_PROJECT ?= $(COMPOSE_PROJECT)-harness
HARNESS_ENV := COMPOSE_PROJECT_NAME=$(HARNESS_PROJECT) TELEGRAM_BOT_TOKEN= LLM_PROVIDER=fake \
	POSTGRES_PORT=0 CORE_PORT=0 PANEL_PORT=0 PHOENIX_UI_PORT=0 PHOENIX_OTLP_GRPC_PORT=0

harness: docker-ready ## Guiones E2E en un stack aparte y efímero (agente fake, sin bot), como CI
	@$(HARNESS_ENV) $(COMPOSE) run --rm --build harness; status=$$?; \
	$(HARNESS_ENV) $(COMPOSE) down -v --remove-orphans >/dev/null 2>&1; exit $$status

live-run: ## Corrida real (OBS-6) contra el stack levantado: make live-run ARGS="--sin-frases"
	CORE_URL=http://127.0.0.1:$(CORE_PORT) PANEL_URL=http://127.0.0.1:$(PANEL_PORT) \
	  POSTGRES_PORT=$(POSTGRES_PORT) $(PY) harness/scripts/live_run.py $(ARGS)

# conversar.py y exportar.py van al core en localhost:8000 y al proyecto de compose por defecto.
conversar: ## Ensayo automático de la demo: make conversar ARGS="<escenario>"
	$(PY) scripts/demo/conversar.py $(ARGS)

exportar: ## Exporta las transcripciones a tmp/demo/: make exportar ARGS="<minutos>"
	$(PY) scripts/demo/exportar.py $(ARGS)

smoke: ## Un evento de ejemplo a POST /events del core
	curl -sf -X POST "http://localhost:$(CORE_PORT)/events" -H "Content-Type: application/json" \
	  -d @contracts/examples/event.valid.json && echo
