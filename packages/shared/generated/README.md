# Generated Frontend Types

Use this folder for generated TypeScript types or clients consumed by the Next.js frontend.

Source of truth:

- FastAPI OpenAPI export in `packages/shared/openapi`
- Pydantic schemas in `services/api/app/schemas`

Rules:

- Do not manually edit generated files.
- Regenerate after backend contract changes.
- Keep generated files split or configured to minimize merge conflicts.

