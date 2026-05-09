# Backend API

Owner: Harsha

This folder contains the backend API, database access, RAG, workflow automation, and integration adapters.

Write here for:

- FastAPI routes
- Pydantic schemas
- SQLAlchemy models
- Alembic migrations
- uv dependency management
- Ruff linting and formatting
- pytest backend tests
- Voice tool endpoints
- Dashboard APIs
- RAG ingestion and retrieval
- Calendar and email adapters
- Background jobs
- Backend tests

Do not write here for:

- Frontend UI
- Voice agent runtime behavior
- Actual secret values

Backend contract source of truth should live in `app/schemas`. Exported OpenAPI artifacts should go in `packages/shared/openapi`, generated frontend types should go in `packages/shared/generated`, and human-readable contracts should be documented in `docs/contracts`.
