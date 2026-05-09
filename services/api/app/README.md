# FastAPI App

Owner: Harsha

This folder contains the Python FastAPI application.

Backend toolchain:

- Python 3.12
- FastAPI
- Uvicorn
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- uv
- Ruff
- pytest

Suggested layout:

- `api`: route modules
- `core`: configuration, security helpers, app setup
- `db`: database sessions and migration helpers
- `models`: SQLAlchemy models
- `schemas`: Pydantic request and response schemas
- `services`: business services
- `integrations`: calendar, email, storage, and provider adapters
- `rag`: document processing, embeddings, and retrieval
- `jobs`: background jobs
