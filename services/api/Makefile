# Waxwing API — developer convenience targets
# Requires: uv (https://docs.astral.sh/uv/)
# On Windows: use Git Bash, WSL, or run `uv run <command>` directly

.PHONY: run lint format test migrate migrate-create install

# Install all dependencies (runtime + dev) into the uv-managed venv
install:
	uv sync --all-groups

# Start the API with hot reload
run:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Check linting and formatting without modifying files (used in CI)
lint:
	uv run ruff check .
	uv run ruff format --check .

# Auto-fix lint issues and reformat (for local dev)
format:
	uv run ruff check --fix .
	uv run ruff format .

# Run the test suite
test:
	uv run pytest

# Apply all pending Alembic migrations
migrate:
	uv run alembic upgrade head

# Create a new migration: make migrate-create name=add_leads
migrate-create:
	uv run alembic revision --autogenerate -m "$(name)"
