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

info: ## URLs de core, panel (usuarios por asesor y gerente), negocio y phoenix
	@if ! $(PSQL) -Atc "select 1" >/dev/null 2>&1; then \
	  echo "postgres no está arriba: make up"; exit 1; fi
	@echo
	@echo "Core:     http://localhost:$(CORE_PORT)/health"
	@echo "Phoenix:  http://localhost:$(PHOENIX_UI_PORT)"
	@echo "Catálogo: $$($(PSQL) -Atc 'select count(*) from properties') propiedades"
	@echo "Panel:    http://localhost:$(PANEL_PORT)/login  (B-17: usuario y contraseña)"
	@$(PSQL) -Atc "select u.username, u.role, coalesce(a.name, ''), coalesce(a.zone, '') from panel_users u \
	  left join advisors a on a.id = u.advisor_id order by u.role, u.username" | \
	  while IFS='|' read -r username role name zone; do \
	    echo "  $$username  ($$role$${name:+: $$name, $$zone})"; \
	  done
	@echo "  contraseñas de demo: PANEL_ADVISOR_PASSWORD y PANEL_MANAGER_PASSWORD (.env.example)"
	@users=$$($(PSQL) -Atc "select count(*) from panel_users"); \
	[ "$$users" = "0" ] && echo "  (sin usuarios: make seed)" || \
	echo "Negocio:  http://localhost:$(PANEL_PORT)/negocio?demo=1  (como gerente)"
