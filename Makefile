SHELL := /bin/bash
COMPOSE := docker compose
MODE ?= off

.PHONY: help env up down logs ps chaos smoke reset

help: ## Show available targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

env: ## Create .env with random secrets (if missing)
	@./scripts/init_env.sh

up: env ## Build and start the whole stack
	$(COMPOSE) up -d --build

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

chaos: ## Switch chaos mode: make chaos MODE=off|slow_queries|lock_contention|traffic_spike|brute_force|payment_failures
	$(COMPOSE) exec -T load-generator python -m app.ctl chaos $(MODE)

smoke: ## Run end-to-end smoke test
	./scripts/smoke_test.sh

reset: ## Destroy containers AND volumes, then start from scratch
	$(COMPOSE) down -v --remove-orphans
	$(MAKE) up
