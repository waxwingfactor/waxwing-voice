# Waxwing Voice Folder Structure and Ownership

## 1. Purpose

This folder structure keeps each developer's daily work mostly inside their own area while giving the team clear places for shared contracts, infrastructure, docs, tests, and scripts.

The goal is to reduce merge conflicts by separating ownership boundaries:

- Alex writes mostly in `apps/web`
- Harsha writes mostly in `services/api`
- Akhil writes mostly in `services/voice-agent`
- Subbu writes mostly in `infra`
- Backend schemas live in `services/api/app/schemas`
- OpenAPI and generated frontend types live in `packages/shared`
- Human-readable contracts live in `docs/contracts`

## 2. Root Structure

```text
apps/
  web/
services/
  api/
  voice-agent/
packages/
  shared/
    openapi/
    generated/
infra/
  environments/
    local/
    staging/
    pilot/
  scripts/
docs/
  adr/
  contracts/
  runbooks/
  team/
tests/
  e2e/
  fixtures/
scripts/
.github/
  workflows/
```

## 3. Ownership Map

| Folder | Primary owner | Purpose |
| --- | --- | --- |
| `apps/web` | Alex | Next.js frontend dashboard |
| `services/api` | Harsha | Backend API, database, RAG, workflows |
| `services/voice-agent` | Akhil | LiveKit voice agent, STT, LLM, TTS |
| `packages/shared` | Shared | OpenAPI artifacts, generated frontend types, shared constants |
| `infra` | Subbu | Environments, deployment, provider setup, secrets templates |
| `docs` | Shared | Product, implementation, guardrails, contracts, runbooks |
| `tests/e2e` | Shared | Cross-system tests owned by whoever changes the workflow |
| `tests/fixtures` | Shared | Sample property data, test documents, mock payloads |
| `scripts` | Shared | Repo-level developer scripts |
| `.github/workflows` | Subbu | CI/CD workflows |

## 4. Conflict Prevention Rules

### 4.1 Stay In Your Primary Folder

Default write areas:

- Alex: `apps/web`
- Harsha: `services/api`
- Akhil: `services/voice-agent`
- Subbu: `infra`, `.github/workflows`

If you need to edit another person's primary folder, tell that owner first and keep the change small.

### 4.2 Use Shared Contracts For Integration

Do not copy-paste API shapes across services. Put shared schemas and examples in:

- `services/api/app/schemas`
- `packages/shared/openapi`
- `packages/shared/generated`
- `docs/contracts`

Use shared contracts for:

- Voice tool request and response shapes
- Dashboard API response shapes
- Webhook payload examples
- Error codes
- Status enums

### 4.3 Avoid Shared File Hotspots

Files that commonly cause merge conflicts should be kept small and split by domain.

Prefer:

```text
services/api/app/schemas/calls.py
services/api/app/schemas/leads.py
services/api/app/schemas/bookings.py
services/api/app/schemas/documents.py
services/api/app/schemas/errors.py
```

Generated frontend types may then be exported as:

```text
packages/shared/generated/calls.ts
packages/shared/generated/leads.ts
packages/shared/generated/bookings.ts
packages/shared/generated/documents.ts
packages/shared/generated/errors.ts
```

Avoid:

```text
services/api/app/schemas/all_schemas.py
```

as the only place where everyone edits all contracts.

### 4.4 One Feature, One Owner, One Main Folder

When implementing a feature, choose the primary folder before coding.

Examples:

- Voice booking flow: Akhil starts in `services/voice-agent`
- Booking endpoint: Harsha starts in `services/api`
- Booking UI: Alex starts in `apps/web`
- Calendar OAuth setup: Subbu starts in `infra`

Shared files should only contain the integration contract, not the whole implementation.

### 4.5 Use ADRs For Tool Or Architecture Changes

Architecture decision records go in:

```text
docs/adr/
```

Create an ADR before changing locked tools, adding a new provider, or modifying ownership boundaries.

## 5. Folder Details

### 5.1 `apps/web`

Owner: Alex

Contains:

- Next.js app routes
- React components
- UI state
- Frontend API clients
- Frontend tests
- Dashboard-specific utilities

Should not contain:

- Database queries
- Provider secrets
- Twilio, LiveKit, Gemini, Whisper, VibeVoice SDK usage
- Backend business logic

### 5.2 `services/api`

Owner: Harsha

Contains:

- FastAPI backend
- Pydantic schemas
- SQLAlchemy models
- Alembic migrations
- uv dependency management
- Ruff linting and formatting
- pytest backend tests
- API routes
- Voice tool endpoints
- RAG ingestion and retrieval
- Calendar and email adapters
- Background jobs
- Backend tests

Should not contain:

- Frontend UI code
- Voice prompt experiments unrelated to backend contracts
- Infrastructure secrets

### 5.3 `services/voice-agent`

Owner: Akhil

Contains:

- LiveKit Agent code
- Whisper integration
- Gemini-3.0 Flash orchestration
- VibeVoice integration
- Voice prompts
- Call state
- Voice tool client
- Voice smoke tests

Should not contain:

- Direct database writes
- Dashboard UI
- Provider account setup docs that belong in `infra` or `docs/runbooks`

### 5.4 `packages/shared`

Owner: shared, with review from affected owners

Contains:

- Exported FastAPI OpenAPI artifacts
- Generated frontend TypeScript types
- Shared status enums
- Status enums
- Error codes
- Shared validation helpers, if needed

Rules:

- Keep files split by domain.
- Do not put backend implementation logic here.
- Do not manually edit generated files.
- Changes here should be treated as integration changes.
- If a shared contract changes, update the backend Pydantic schema, generated types, and contract docs.

### 5.5 `infra`

Owner: Subbu

Contains:

- Environment templates
- Deployment scripts
- Provider setup notes
- Infrastructure configuration
- Secret name documentation
- Monitoring setup

Should not contain:

- Actual secret values
- Product business logic
- Frontend page code
- Backend route logic

### 5.6 `docs`

Owner: shared

Contains:

- Product documents
- Implementation plans
- Guardrails
- Team docs
- ADRs
- API and tool contracts
- Runbooks

Rules:

- Product and planning docs can be edited by anyone.
- Owner-specific docs should be edited by the owner or with the owner's agreement.
- Contract docs should be updated with the code that changes the contract.

### 5.7 `tests`

Owner: shared

Contains:

- `tests/e2e`: cross-system tests
- `tests/fixtures`: sample data, property documents, mock payloads

Rules:

- Put service-specific tests inside the service folder.
- Put only cross-system tests at root.

### 5.8 `scripts`

Owner: shared

Contains:

- Repo-level setup and utility scripts

Rules:

- Service-specific scripts should stay inside the service folder.
- Scripts that affect multiple services belong here.

## 6. Recommended Feature Workflow

1. Define or update the backend Pydantic schema in `services/api/app/schemas`.
2. The service owner implements their part in their primary folder.
3. Export OpenAPI and regenerate frontend types when needed.
4. Consumers update against the contract.
5. Add or update tests in the service folder or `tests/e2e`.
6. Update runbooks or owner docs if setup or behavior changed.

## 7. Examples

### Example: Lead Capture

- Akhil: `services/voice-agent` for conversation flow
- Harsha: `services/api` for lead endpoint and database write
- Alex: `apps/web` for lead display
- Backend schema: `services/api/app/schemas/leads.py`
- Generated frontend type: `packages/shared/generated/leads.ts`
- Docs: `docs/contracts/leads.md`

### Example: Document Upload

- Alex: `apps/web` upload UI
- Harsha: `services/api` upload endpoint and processing
- Subbu: `infra` object storage config
- Backend schema: `services/api/app/schemas/documents.py`
- Generated frontend type: `packages/shared/generated/documents.ts`
- Docs: `docs/contracts/documents.md`

### Example: Tour Booking

- Akhil: `services/voice-agent` booking conversation
- Harsha: `services/api` calendar availability and booking
- Alex: `apps/web` booking status UI
- Subbu: `infra` Google Calendar OAuth setup
- Backend schema: `services/api/app/schemas/bookings.py`
- Generated frontend type: `packages/shared/generated/bookings.ts`
- Docs: `docs/contracts/bookings.md`
