# Shared Package

Owner: shared

This folder contains code and contracts used by more than one part of the system.

Use this folder for:

- Exported FastAPI OpenAPI artifacts
- Generated frontend TypeScript types
- Status enums
- Error codes
- Small shared validation helpers

Avoid putting implementation logic here. Backend contract source of truth belongs in `services/api/app/schemas` as Pydantic models.

Conflict prevention:

- Split files by domain.
- Avoid one giant shared file.
- Treat changes here as integration changes.
- Do not manually edit generated files.
