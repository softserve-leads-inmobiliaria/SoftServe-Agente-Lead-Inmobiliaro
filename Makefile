# Atajos para levantar, sembrar y probar el proyecto (#192).
#
# Solo envuelve los comandos que ya existen (docker compose, scripts/, pytest): todo sigue funcionando
# sin make. Cada área vive en su módulo de make/; `make help` lista los objetivos por sección.
#
# Compatible con GNU Make 3.81 (el de macOS) y con Windows desde Git Bash.

.DEFAULT_GOAL := help
# Los atajos encadenan pasos que dependen del anterior (levantar, sembrar, mostrar).
.NOTPARALLEL:

include make/config.mk
include make/setup.mk
include make/stack.mk
include make/db.mk
include make/test.mk
include make/demo.mk

##@ Atajos

.PHONY: help start stop reset

help: ## Lista los objetivos por sección
	@awk 'BEGIN {FS = ":.*## "} \
	  /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
	  /^[a-zA-Z0-9_%-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: s=<servicio>  ARGS=\"...\"  SERVICES=\"core panel phoenix\"  COMPOSE=\"docker compose\""

start: env up seed-all info ## Todo desde cero: stack + asesores, catálogo e historia (N-9) + URLs

stop: down ## Apaga el stack (conserva los datos)

reset: clean start ## Borra contenedores y volúmenes y vuelve a empezar
