# Harsha Project Document: Backend

## 1. Mission

Harsha owns the backend system that makes Waxwing Voice reliable, auditable, and useful. The backend is the source of truth for properties, calls, leads, documents, bookings, emails, and workflow state.

Primary scope:

- Backend API
- Database schema
- Voice tool endpoints
- Dashboard endpoints
- RAG ingestion and retrieval
- Calendar and email adapters
- Validation
- Audit logging
- Background jobs

## 2. Locked Tools

Use:

- Python
- FastAPI
- Uvicorn
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- uv
- Ruff
- pytest
- PostgreSQL
- pgvector
- S3-compatible object storage
- One selected transactional email provider
- Google Calendar first

Do not introduce:

- Another backend framework
- Another ORM
- Another database
- Another vector database
- Direct provider calls from the frontend
- Direct database writes from the voice agent
- Node.js, Fastify, Express, NestJS, Prisma, or TypeORM for the MVP backend

## 3. Deliverables

### Phase 0

- Backend service skeleton
- `pyproject.toml` dependency and tool configuration plan
- SQLAlchemy model draft
- Alembic migration draft
- Pydantic schema draft
- API contract draft
- Voice tool endpoint list
- Seed data for one sample property
- `.env.example` entries with Subbu

### Phase 1

- Property read endpoint
- Call create/update endpoint
- Transcript segment endpoint
- Voice event ingestion endpoint
- Database migrations for properties and calls
- Basic dashboard API for calls

### Phase 2

- Leads, bookings, emails, documents, and audit schema
- Lead create/update endpoint
- Call summary endpoint
- Dashboard APIs for calls, leads, properties, and summaries
- Request validation schemas
- Error response standard

### Phase 3

- Document upload endpoint
- Object storage integration
- Document parsing job
- Chunking and embedding pipeline
- pgvector storage
- Property-scoped retrieval endpoint
- Document processing statuses
- Re-index endpoint

### Phase 4

- Calendar availability adapter
- Calendar booking endpoint
- Email template storage
- Email delivery endpoint
- Human handoff endpoint
- Audit logging for bookings, emails, handoffs, and settings

### Phase 5

- Authorization checks
- Integration tests
- Retry handling for background jobs
- API rate limiting where appropriate
- Data inspection tools for pilot support
- Backup and restore support with Subbu

## 4. API Contract Responsibilities

Harsha owns request and response schemas for:

- Voice tools
- Dashboard data
- Document uploads
- Settings
- Calendar booking
- Email delivery
- Handoff actions

Every endpoint should define:

- Method and path
- Auth requirement
- Pydantic request schema
- Pydantic response schema
- Error schema
- Example request
- Example response
- Idempotency behavior, if applicable

## 5. Core Models

Minimum models:

- Company
- User
- Property
- Document
- KnowledgeChunk
- Call
- Lead
- Booking
- Email
- AuditLog

Every model should include:

- Stable ID
- Company or property scoping where applicable
- Created timestamp
- Updated timestamp where applicable

## 6. RAG Requirements

The RAG system must:

- Store original files in object storage
- Track document processing status
- Parse document text
- Chunk text into searchable units
- Create embeddings
- Store embeddings in pgvector
- Scope retrieval by property
- Return source labels and confidence metadata
- Support re-indexing when documents change

The retrieval API should not return content from another property or company.

## 7. Workflow Requirements

Durable actions must go through backend workflows:

- Lead creation or update
- Tour availability lookup
- Tour booking
- Follow-up email sending
- Handoff notification
- Call summary saving
- Document processing

Use audit logs for:

- Document upload
- Document delete
- Re-index
- Booking
- Email send
- Handoff
- Settings update

## 8. Dependencies

Depends on Akhil for:

- Voice tool usage needs
- Call lifecycle events
- Lead extraction payloads
- Summary payloads
- Latency-sensitive endpoints

Depends on Alex for:

- Dashboard field requirements
- Filter and sorting needs
- Upload UX needs
- Settings page needs

Depends on Subbu for:

- PostgreSQL and pgvector
- Object storage
- Email provider credentials
- Calendar OAuth credentials
- Background job runtime
- Deployment and logs

## 9. Validation and Error Rules

Backend should:

- Validate every request body
- Return structured errors
- Include a stable error code
- Avoid exposing secrets
- Avoid PII-heavy logs
- Keep external provider errors understandable
- Make voice tool errors actionable for Akhil

Example error shape:

```json
{
  "error": {
    "code": "BOOKING_SLOT_UNAVAILABLE",
    "message": "The selected tour slot is no longer available.",
    "retryable": false
  }
}
```

## 10. Definition of Done

A backend feature is done when:

- It has a schema
- It validates input
- It scopes data by company or property
- It has a success path and error path
- It writes audit logs for sensitive actions
- It has seed or test data where useful
- It passes Ruff checks
- It has pytest coverage or a documented manual test
- It is documented for Akhil or Alex if they consume it
- It runs in staging with Subbu's environment
