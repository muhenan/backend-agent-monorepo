.DEFAULT_GOAL := help
.PHONY: help up down logs migrate makemigrations shell createsuperuser db

help: ## Show the available commands
	@echo "Commands: up, down, logs, migrate, makemigrations, shell, createsuperuser, db"

up: ## Build the app image and start Django + PostgreSQL
	docker compose up --build

down: ## Stop the containers (keep the database volume)
	docker compose down

logs: ## Follow app and database logs
	docker compose logs -f web db

migrate: ## Apply Django database migrations
	docker compose exec web uv run --no-sync python manage.py migrate

makemigrations: ## Create migrations after changing Django models
	docker compose exec web uv run --no-sync python manage.py makemigrations

shell: ## Open the Django shell inside the app container
	docker compose exec web uv run --no-sync python manage.py shell

createsuperuser: ## Create a Django admin user
	docker compose exec web uv run --no-sync python manage.py createsuperuser

db: ## Open a psql shell in PostgreSQL
	docker compose exec db sh -lc 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
