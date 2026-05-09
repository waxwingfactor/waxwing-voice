# OpenAPI Artifacts

Use this folder for exported OpenAPI schemas from the FastAPI backend.

Source of truth:

- `services/api/app/schemas`
- FastAPI route definitions in `services/api/app/api`

Rules:

- Do not manually edit generated OpenAPI files.
- Regenerate OpenAPI after backend contract changes.
- Keep environment-specific server URLs out of generated artifacts unless intentionally required.

