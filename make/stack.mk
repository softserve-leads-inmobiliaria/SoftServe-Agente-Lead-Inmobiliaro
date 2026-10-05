##@ Stack (docker compose)

.PHONY: up up-infra rebuild down clean restart logs ps build shell info

up: docker-ready ## Levanta core, panel y phoenix (más postgres, catalog y migrate) y espera que estén sanos
	$(COMPOSE) up -d --build --wait $(SERVICES)

up-infra: docker-ready ## Solo postgres y phoenix
	$(COMPOSE) up -d --wait postgres phoenix

rebuild: docker-ready ## Reconstruye y reinicia un servicio: make rebuild s=core
	$(COMPOSE) up -d --build --wait $(or $(s),$(SERVICES))

down: ## Apaga el stack (conserva los volúmenes)
	$(COMPOSE) down --remove-orphans

clean: ## Apaga y BORRA los volúmenes (base de datos y trazas de phoenix)
	@echo "==> Borrando contenedores y volúmenes (pgdata, phoenixdata)"
	$(COMPOSE) down -v --remove-orphans

restart: ## Reinicia sin reconstruir: make restart s=core (sin s, todos)
	$(COMPOSE) restart $(s)

logs: ## Sigue los logs: make logs s=core (sin s, todos)
	$(COMPOSE) logs -f --tail=$(or $(TAIL),100) $(s)

ps: ## Estado de los contenedores
	$(COMPOSE) ps -a

build: ## Construye las imágenes sin levantar nada
	$(COMPOSE) build

shell: ## Abre una shell en un contenedor: make shell s=core
	$(COMPOSE) exec $(or $(s),core) sh

info: ## URLs de core, panel (por asesor), negocio y phoenix
	@if ! $(PSQL) -Atc "select 1" >/dev/null 2>&1; then \
	  echo "postgres no está arriba: make up"; exit 1; fi
	@echo
	@echo "Core:     http://localhost:$(CORE_PORT)/health"
	@echo "Phoenix:  http://localhost:$(PHOENIX_UI_PORT)"
	@echo "Catálogo: $$($(PSQL) -Atc 'select count(*) from properties') propiedades"
	@echo "Paneles de los asesores (la URL cambia en cada seed):"
	@$(PSQL) -Atc "select id, name, zone, access_token from advisors order by id" | \
	  while IFS='|' read -r id name zone token; do \
	    echo "  $$id  $$name ($$zone)  http://localhost:$(PANEL_PORT)/panel/$$token"; \
	  done
	@token=$$($(PSQL) -Atc "select access_token from advisors order by id limit 1"); \
	[ -z "$$token" ] && echo "  (sin asesores: make seed)" || \
	echo "Negocio:  http://localhost:$(PANEL_PORT)/panel/$$token/negocio?demo=1"
