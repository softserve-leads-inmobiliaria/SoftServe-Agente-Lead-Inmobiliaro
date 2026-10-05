# Variables comunes a todos los módulos. Cualquiera se puede pisar desde la línea de comandos o el
# entorno: `make up SERVICES=core`, `make test COMPOSE="docker-compose"`.

# Git Bash en Windows y /bin/bash en macOS/Linux. Make 3.81 no tiene .SHELLFLAGS: el `pipefail` se
# pone en la receta que lo necesite.
SHELL := bash

# --- Python ----------------------------------------------------------------------------------------
# En Windows el venv guarda los binarios en Scripts/ y `python3` puede ser un shim vacío.
ifeq ($(OS),Windows_NT)
VENV_BIN ?= .venv/Scripts
SYS_PYTHON ?= python
else
VENV_BIN ?= .venv/bin
SYS_PYTHON ?= python3
endif
VENV_PY := $(VENV_BIN)/python
# El del venv si existe; si no, el del sistema (por ejemplo, con el venv ya activado).
PY ?= $(if $(wildcard $(VENV_BIN)/python*),$(VENV_PY),$(SYS_PYTHON))

# --- Docker ----------------------------------------------------------------------------------------
COMPOSE ?= docker compose
# Nunca `up` a secas: arrancaría también el arnés y el seed (ver la cabecera de docker-compose.yml).
SERVICES ?= core panel phoenix

# --- Valores del .env ------------------------------------------------------------------------------
# Como en scripts/demo/up.sh: la clave del .env (sin el \r de Windows) o el default del compose.
env_value = $(or $(shell [ -f .env ] && grep -E '^$(1)=' .env | tail -n1 | cut -d= -f2- | tr -d '\r'),$(2))

CORE_PORT ?= $(call env_value,CORE_PORT,8000)
PANEL_PORT ?= $(call env_value,PANEL_PORT,8080)
PHOENIX_UI_PORT ?= $(call env_value,PHOENIX_UI_PORT,6006)
POSTGRES_USER ?= $(call env_value,POSTGRES_USER,leads)
POSTGRES_DB ?= $(call env_value,POSTGRES_DB,leads)

PSQL = $(COMPOSE) exec -T postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

# Argumentos extra para el comando que envuelve el objetivo: `make test-core ARGS="-k consent"`.
ARGS ?=
