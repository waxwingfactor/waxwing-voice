# Pydantic Schemas

Owner: Harsha

This folder is the backend source of truth for API request and response schemas.

Rules:

- Split schemas by domain.
- Keep voice tool schemas explicit and validated.
- Update OpenAPI exports and generated frontend types after schema changes.

Recommended files:

- `calls.py`
- `leads.py`
- `bookings.py`
- `documents.py`
- `emails.py`
- `errors.py`
- `settings.py`
- `voice_tools.py`

