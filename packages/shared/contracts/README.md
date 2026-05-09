# Shared Contracts

Owner: shared

This folder is retained for hand-written shared contract helpers only.

For the Python FastAPI backend, the source of truth for API contracts is:

- `services/api/app/schemas` for Pydantic schemas
- `packages/shared/openapi` for exported OpenAPI artifacts
- `packages/shared/generated` for generated frontend TypeScript types

Do not create a parallel hand-written contract system here unless the team agrees through an ADR.

Only if the team explicitly needs hand-written frontend contract helpers, split them by domain:

- `calls.ts`
- `leads.ts`
- `bookings.ts`
- `documents.ts`
- `emails.ts`
- `errors.ts`
- `status.ts`

When a contract changes, update:

- The backend Pydantic schema
- The exported OpenAPI artifact
- The generated frontend type, if used
- The owner implementation and consumer implementation
- The matching file in `docs/contracts`
