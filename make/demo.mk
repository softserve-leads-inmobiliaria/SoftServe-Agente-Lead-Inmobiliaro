##@ Demo y arnés

.PHONY: demo demo-down demo-purge harness live-run conversar exportar smoke

demo: ## Demo en vivo: stack + seeds + comprobaciones + enlaces (scripts/demo/up.sh)
	bash scripts/demo/up.sh

demo-down: ## Exporta las conversaciones y apaga la demo (scripts/demo/down.sh)
	bash scripts/demo/down.sh

demo-purge: ## Como demo-down y además borra las imágenes
	bash scripts/demo/down.sh --purge

harness: docker-ready ## Guiones E2E contra los contenedores (agente fake)
	$(COMPOSE) run --rm --build harness

live-run: ## Corrida real (OBS-6) contra el stack levantado: make live-run ARGS="--sin-frases"
	$(PY) harness/scripts/live_run.py $(ARGS)

conversar: ## Ensayo automático de la demo: make conversar ARGS="<escenario>"
	$(PY) scripts/demo/conversar.py $(ARGS)

exportar: ## Exporta las transcripciones a tmp/demo/: make exportar ARGS="<minutos>"
	$(PY) scripts/demo/exportar.py $(ARGS)

smoke: ## Un evento de ejemplo a POST /events del core
	curl -sf -X POST "http://localhost:$(CORE_PORT)/events" -H "Content-Type: application/json" \
	  -d @contracts/examples/event.valid.json && echo
