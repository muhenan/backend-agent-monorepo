.PHONY: setup up down restart logs shell lock lint format test

setup:
	@test -f .env || cp .env.example .env
	uv lock

up:
	@test -f .env || (echo "缺少 .env，请先运行 make setup" && exit 1)
	docker compose up --build -d
	@echo "RAG 学习 Web: http://localhost:$${APP_PORT:-8002}"

restart:
	docker compose up --build -d --force-recreate

down:
	docker compose down

logs:
	docker compose logs -f app qdrant

shell:
	docker compose exec app /bin/bash

lock:
	uv lock

lint:
	docker compose run --rm --no-deps --build app uv sync --group dev && docker compose run --rm --no-deps app uv run --group dev ruff check src tests

format:
	docker compose run --rm --no-deps --build app uv sync --group dev && docker compose run --rm --no-deps app uv run --group dev ruff format src tests

test:
	docker compose run --rm --build app uv sync --group dev && docker compose run --rm app uv run --group dev pytest
