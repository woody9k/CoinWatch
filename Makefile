.PHONY: standards-check test api engine web migrate

standards-check:
	uv run ruff check
	uv run ruff format --check
	uv run pyright
	uv run pytest
	cd web && pnpm exec tsc --noEmit
	cd web && pnpm exec eslint .

test:
	uv run pytest

api:
	uv run python -m coinwatch.api

engine:
	uv run python -m coinwatch.engine

web:
	cd web && pnpm dev

migrate:
	uv run alembic upgrade head
