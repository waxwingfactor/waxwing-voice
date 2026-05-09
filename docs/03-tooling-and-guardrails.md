# Waxwing Voice Tooling and Guardrails

## 1. Purpose

This document keeps the MVP focused. Each developer should follow these tool choices and scope boundaries unless the team writes and accepts an architecture decision record.

## 2. Locked Tools

| Category | Use this | Do not introduce during MVP |
| --- | --- | --- |
| Telephony | Twilio | Plivo, Vonage, SignalWire, custom PSTN providers |
| Voice orchestration | LiveKit Agents | Custom WebRTC orchestration, alternate agent frameworks |
| STT | Whisper | Deepgram, AssemblyAI, Google STT, browser STT |
| LLM | Gemini-3.0 Flash | OpenAI, Anthropic, local LLMs, multi-model routing |
| TTS | VibeVoice | ElevenLabs, Azure TTS, Google TTS, browser TTS |
| Frontend | Next.js, React, TypeScript | Angular, Vue, Svelte, plain jQuery |
| Frontend styling | Tailwind CSS and local components | Bootstrap, Material UI, multiple design systems |
| Icons | lucide-react | Inline custom icon sets unless necessary |
| Backend | Python, FastAPI | Node.js, Fastify, Express, NestJS, Django, Rails, Go services |
| Backend runtime | Uvicorn | Gunicorn-only setup before pilot need is clear |
| Python version | Python 3.12 | Multiple Python versions across developers |
| Python dependency management | uv | Poetry, Pipenv, ad hoc global pip installs |
| Python linting and formatting | Ruff | Black plus Flake8 plus isort as separate tools |
| Python testing | pytest | unittest-only custom test runners |
| API validation | Pydantic | Ad hoc dictionaries, unvalidated payloads |
| ORM and migrations | SQLAlchemy, Alembic | Prisma, Sequelize, TypeORM, direct SQL everywhere |
| Relational DB | PostgreSQL | MySQL, MongoDB, DynamoDB |
| Vector DB | pgvector | Pinecone, Qdrant, Weaviate, Chroma |
| Storage | S3-compatible object storage | Local-only file storage for shared environments |
| Email | One provider selected in Phase 0 | Multiple email vendors at the same time |
| Calendar | Google Calendar first | Multiple calendar providers before booking is stable |
| Infrastructure | Simple reproducible deploy scripts first | Complex orchestration before pilot need exists |

## 3. Allowed Language Boundaries

- Python is the default language for the backend and voice agent.
- TypeScript is the default language for the frontend.
- Backend API schemas should be defined with Pydantic and exposed through FastAPI OpenAPI.
- Frontend TypeScript API types may be generated from the FastAPI OpenAPI schema.
- Do not add another language runtime without an architecture decision record.

## 4. Suggested Repo Structure

Use this structure unless the team explicitly changes it. The detailed ownership rules live in [Folder Structure and Ownership](04-folder-structure-and-ownership.md).

```text
apps/
  web/
services/
  api/
  voice-agent/
packages/
  shared/
infra/
docs/
```

Ownership:

- `apps/web`: Alex
- `services/api`: Harsha
- `services/voice-agent`: Akhil
- `infra`: Subbu
- `packages/shared`: generated OpenAPI artifacts and frontend-consumable shared types, reviewed by affected owners
- `docs`: everyone

Conflict prevention rules:

- Keep daily implementation work inside the owner's primary folder.
- Put backend request and response source-of-truth schemas in `services/api/app/schemas`.
- Put exported OpenAPI artifacts in `packages/shared/openapi`.
- Put generated frontend types in `packages/shared/generated`.
- Put human-readable contract docs in `docs/contracts`.
- Split shared files by domain instead of creating one large file that everyone edits.
- Ask the primary owner before editing another person's folder.

## 5. Scope Guardrails

### 5.1 MVP Must Stay Focused On

- Inbound calls
- Leasing and resident Q&A
- Lead capture
- Tour booking
- Follow-up email
- Property knowledge base
- Dashboard review workflow
- Basic escalation

### 5.2 MVP Must Not Expand Into

- Full CRM
- Full PMS integration
- Payment processing
- Resident portal
- Vendor dispatch marketplace
- Native mobile app
- Automated leasing approval decisions
- Multilingual launch scope
- Voice cloning
- Multiple provider comparison framework

## 6. Architecture Change Rules

Write an architecture decision record before:

- Replacing Twilio, LiveKit, Whisper, Gemini-3.0 Flash, or VibeVoice
- Adding a second backend framework
- Adding another database or vector database
- Adding another LLM, STT, or TTS provider
- Changing the voice-to-backend tool contract pattern
- Letting the frontend call provider SDKs directly
- Enabling call recording in pilot production
- Adding PMS or CRM integrations

An architecture decision record should include:

- Decision title
- Date
- Problem
- Options considered
- Decision
- Tradeoffs
- Impacted owners
- Rollback plan

## 7. Voice Agent Guardrails

Akhil must keep the voice agent inside these boundaries:

- Use Twilio only for telephony.
- Use LiveKit Agents for real-time call sessions.
- Use Whisper for STT.
- Use Gemini-3.0 Flash for the LLM.
- Use VibeVoice for TTS.
- Do not write business records directly to the database.
- Use Harsha's backend tools for leads, calls, bookings, emails, handoffs, and retrieval.
- Keep voice responses short enough for a phone conversation.
- Confirm important details before durable actions.
- Log tool failures without exposing secrets or PII-heavy payloads.

## 8. Backend Guardrails

Harsha must keep backend work inside these boundaries:

- Use Python 3.12, FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, PostgreSQL, and pgvector.
- Use uv for backend dependency management.
- Use Ruff for linting and formatting.
- Use pytest for backend tests.
- Validate every request body.
- Treat voice tool endpoints as production APIs, not internal experiments.
- Keep property/company scoping on every query.
- Use transactions for multi-step durable actions.
- Add audit logs for document uploads, bookings, emails, handoffs, and settings changes.
- Keep external provider calls behind adapters.
- Do not let the voice agent bypass backend validation.
- Do not introduce a separate vector database during MVP.

## 9. Frontend Guardrails

Alex must keep frontend work inside these boundaries:

- Use Next.js, React, TypeScript, Tailwind CSS, and local components.
- Use backend APIs only.
- Do not call Twilio, LiveKit, Gemini, Whisper, VibeVoice, calendar, email, or database providers directly from the browser.
- Use shared types or generated API types when available.
- Build loading, empty, error, and permission states for every data page.
- Keep operational screens dense, clear, and work-focused.
- Avoid marketing-style pages for the internal dashboard.
- Use lucide-react icons for common actions.
- Do not create a second design system.

## 10. DevOps Guardrails

Subbu must keep DevOps work inside these boundaries:

- Keep all secrets out of source control.
- Document every required environment variable in `.env.example`.
- Use separate credentials for local, staging, and pilot production.
- Create provider accounts using shared team ownership, not personal-only ownership.
- Keep Twilio, LiveKit, Gemini, Whisper, VibeVoice, database, storage, email, and calendar credentials clearly named.
- Do not enable call recording until consent language and storage controls are approved.
- Prefer simple reproducible deployment over complex infrastructure during MVP.
- Add monitoring before pilot production.

## 11. AI Safety Guardrails

The agent must not:

- Invent prices, availability, fees, rules, or lease terms
- Make Fair Housing interpretations
- Discuss protected classes in a discriminatory way
- Guarantee approval or eligibility
- Give legal or financial advice
- Make emergency decisions beyond escalation instructions
- Send emails or book tours without confirming key details
- Pretend to know information not present in approved property data

The agent should say:

- "I do not have that information in the property details I can access."
- "I can have the property team follow up on that."
- "Let me confirm the details before I book that."

## 12. Data Privacy Guardrails

- Store only the PII needed for calls, leads, bookings, and follow-up.
- Avoid PII in logs.
- Redact secrets and tokens from all error output.
- Scope all property data by company and property.
- Keep transcript access behind authenticated dashboard access.
- Document retention policy before pilot production.
- Disable recording unless consent and storage policy are approved.

## 13. Testing Guardrails

Minimum test coverage before pilot:

- Backend validation tests for voice tool payloads
- Backend integration tests for lead creation, call summary, booking, and email
- RAG retrieval tests for sample property questions
- Voice smoke tests for leasing inquiry, tour booking, unknown answer, and escalation
- Frontend route smoke tests for dashboard, calls, leads, knowledge, and settings
- Deployment smoke test for staging

## 14. Definition of Done

A feature is done when:

- It uses approved MVP tools
- It has an owner
- It has documented environment variables, if any
- It validates inputs
- It has loading and failure behavior where user-facing
- It writes durable records through the backend where appropriate
- It avoids PII-heavy logs
- It has at least one practical test or repeatable manual test
- It is visible in staging, if it is part of the running product
- It does not expand MVP scope without a decision record
