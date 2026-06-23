# Operator helpers for the multi-app box — install to /opt/box/Makefile, run as `deploy`.
# Encodes the two-file colocation overlay once so you never retype it.
#   make deploy app=reviews   |   make logs app=reviews   |   make backup app=reviews
APP_DIR = /opt/apps/$(app)
COMPOSE = docker compose -f docker-compose.yml -f deploy/compose.colocate.yml

guard:
	@test -n "$(app)" || { echo "usage: make <target> app=<name>"; exit 1; }

deploy: guard          ## pull latest + rebuild + restart one app, then show health
	cd $(APP_DIR) && git pull --ff-only && $(COMPOSE) up -d --build && $(COMPOSE) ps

logs: guard            ## tail the web service logs
	cd $(APP_DIR) && $(COMPOSE) logs -f web

ps: guard
	cd $(APP_DIR) && $(COMPOSE) ps

restart: guard
	cd $(APP_DIR) && $(COMPOSE) restart

down: guard
	cd $(APP_DIR) && $(COMPOSE) down

backup: guard          ## pg_dump (apps that ship a `db` service) — run BEFORE schema changes
	cd $(APP_DIR) && $(COMPOSE) exec -T db pg_dump -U $$POSTGRES_USER -Fc $$POSTGRES_DB \
	  > /opt/box/backups/$(app)-$$(date +%F-%H%M).dump

reload-caddy:          ## validate + graceful reload after adding/editing a vhost snippet
	caddy validate --config /opt/caddy/Caddyfile && sudo systemctl reload caddy
