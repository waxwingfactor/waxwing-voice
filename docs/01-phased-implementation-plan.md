# Waxwing Voice Phased Implementation Plan

## Planning Assumptions

- Team size: four people
- Owners: Akhil, Harsha, Alex, Subbu
- MVP telephony: Twilio
- MVP voice stack: LiveKit Agents, Whisper, Gemini-3.0 Flash, VibeVoice
- MVP app stack: Next.js frontend, Python FastAPI backend, Uvicorn, PostgreSQL, pgvector, SQLAlchemy, Alembic, Pydantic
- Python tooling: Python 3.12, uv, Ruff, pytest
- Phase durations are estimates. Exit criteria matter more than calendar dates.

## Phase 0: Project Setup and Architecture Lock

Estimated duration: 2 to 4 days

### Goals

- Establish repo structure
- Create account access
- Lock shared tool decisions
- Define API and data contracts before feature work spreads
- Create a sample property dataset for local and staging testing

### Akhil

- Create voice service skeleton under `services/voice-agent`
- Confirm LiveKit Agent runtime approach
- Define initial call state model
- Draft voice tool contract needs for Harsha
- Define minimum prompt structure for Gemini-3.0 Flash

### Harsha

- Create backend skeleton under `services/api`
- Add `pyproject.toml` plan for FastAPI, Uvicorn, SQLAlchemy, Alembic, Pydantic, Ruff, and pytest
- Define SQLAlchemy model and Alembic migration draft
- Define Pydantic request and response schemas
- Define voice tool endpoint contracts
- Define dashboard API contract outline
- Create seed data plan for one property

### Alex

- Create frontend skeleton under `apps/web`
- Define dashboard route map
- Create low-fidelity screen inventory
- Identify required API shapes for each page
- Add shared UI conventions

### Subbu

- Set up Twilio account, number, and SIP or approved bridge plan
- Set up LiveKit environment
- Set up Gemini API access
- Set up Whisper credentials or deployment path
- Set up VibeVoice access or deployment path
- Provision development PostgreSQL with pgvector
- Select one email provider for MVP and document it
- Create `.env.example` contract with Harsha and Akhil

### Exit Criteria

- Repo folders exist
- Environment variables are listed
- Tooling choices are documented
- One sample property exists as seed data
- Team agrees on API contract ownership

## Phase 1: End-to-End Voice Foundation

Estimated duration: 1 to 2 weeks

### Goals

- Prove one real inbound phone call can reach the AI voice agent
- Prove voice loop: caller speech -> STT -> Gemini-3.0 Flash -> TTS -> caller
- Save basic call records and transcript segments

### Akhil

- Connect Twilio inbound call to LiveKit Agent
- Integrate Whisper STT
- Integrate Gemini-3.0 Flash
- Integrate VibeVoice TTS
- Implement interruption and silence basics
- Emit transcript and call lifecycle events
- Add a minimal system prompt for leasing assistant behavior

### Harsha

- Implement call create/update endpoints
- Implement transcript segment storage
- Implement property profile read endpoint
- Implement voice event ingestion endpoint
- Add database migrations for properties and calls

### Alex

- Build dashboard shell
- Build basic call history page
- Build call detail page with transcript placeholder
- Add environment configuration for backend API base URL

### Subbu

- Configure Twilio routing to LiveKit
- Configure staging LiveKit credentials
- Configure secrets for voice and backend services
- Add basic service logging
- Document how to place a test call

### Exit Criteria

- A real phone call reaches the voice agent
- The agent responds using the locked voice stack
- Transcript segments are stored
- The dashboard can display basic call records
- Failures are logged with enough context to debug

## Phase 2: Backend Data Model and Dashboard Foundation

Estimated duration: 1 to 2 weeks

### Goals

- Stabilize core product records
- Build usable dashboard views
- Prepare data model for leads, bookings, documents, and emails

### Akhil

- Update voice tool usage to match Harsha's validated contracts
- Add lead capture conversation states
- Add structured call summary payload generation
- Add low-confidence and escalation triggers

### Harsha

- Implement properties, leads, bookings, emails, documents, and audit tables
- Implement lead create/update endpoints
- Implement call summary endpoint
- Implement dashboard APIs for calls, call detail, leads, and properties
- Add validation schemas for all voice tool inputs

### Alex

- Build Home Dashboard
- Build Calls view and Call Detail view
- Build Leads view
- Build Settings skeleton
- Add loading, empty, and error states
- Use real API data where available

### Subbu

- Add CI checks for frontend and backend
- Add staging database backups or export plan
- Confirm local setup instructions work from a clean checkout
- Create deployment notes for each service

### Exit Criteria

- Calls, leads, properties, and summaries are visible in the dashboard
- Voice agent can create or update leads through backend tools
- Backend rejects malformed tool payloads
- Staging can be deployed reproducibly

## Phase 3: Property Knowledge Base and RAG

Estimated duration: 1 to 2 weeks

### Goals

- Let property managers upload documents and structured facts
- Parse, chunk, embed, and retrieve approved property knowledge
- Make the voice agent answer property-specific questions from RAG

### Akhil

- Integrate `search_property_knowledge` into the agent
- Ground Gemini-3.0 Flash responses in retrieved context
- Add fallback language when knowledge is missing
- Add citation/source metadata to internal traces
- Test common leasing and resident questions

### Harsha

- Implement document upload endpoint
- Store original files in S3-compatible storage
- Implement parsing, chunking, embedding, and pgvector storage
- Implement retrieval endpoint with property scoping
- Add document processing statuses
- Add re-index capability

### Alex

- Build Property Knowledge view
- Add document upload UI
- Show processing status and last indexed time
- Build structured property facts editor
- Add knowledge health indicators

### Subbu

- Provision object storage bucket
- Configure storage credentials and CORS where needed
- Configure background job runtime
- Add observability for document processing failures
- Verify pgvector is enabled in staging

### Exit Criteria

- A manager can upload a sample property PDF or document
- The document is parsed, chunked, embedded, and searchable
- The agent answers common property questions from retrieved content
- The agent says it does not know when approved knowledge is missing
- Dashboard shows document status and indexing health

## Phase 4: Workflow Automation

Estimated duration: 1 to 2 weeks

### Goals

- Complete lead qualification
- Add tour booking
- Add follow-up email
- Add human handoff

### Akhil

- Add conversation paths for booking tours
- Call availability and booking tools
- Confirm details before booking
- Trigger follow-up email tool after eligible calls
- Trigger human handoff for sensitive or failed flows

### Harsha

- Implement calendar availability and booking adapter
- Implement email sending adapter
- Implement email template storage
- Implement booking records and status updates
- Implement handoff records and notification logic
- Add audit logs for bookings and emails

### Alex

- Add booking details to call and lead views
- Add email status to call and lead views
- Build email template settings
- Build escalation contact settings
- Add filters for booked, follow-up sent, and escalated calls

### Subbu

- Create calendar OAuth app and redirect URLs
- Configure email sender domain and SPF/DKIM/DMARC
- Add webhook configuration for email and calendar status if needed
- Document provider account setup and credential rotation

### Exit Criteria

- Voice agent can book a tour on a connected calendar
- Confirmation email can be sent
- Dashboard displays booking and email status
- Escalations create visible action items
- All durable actions are audit logged

## Phase 5: Pilot Readiness

Estimated duration: 1 to 2 weeks

### Goals

- Harden the MVP for a limited pilot
- Improve reliability, observability, and safety
- Prepare demo and onboarding materials

### Akhil

- Tune latency across STT, LLM, and TTS
- Add retry and graceful fallback behavior
- Improve prompt guardrails
- Create a voice test script suite
- Document known failure modes

### Harsha

- Add role-aware authorization checks
- Add API rate limits where appropriate
- Add data export or admin inspection tools for pilot support
- Improve background job retry behavior
- Add integration tests for critical workflows

### Alex

- Polish dashboard usability
- Add responsive layouts
- Add final empty states and error recovery states
- Add basic analytics cards
- Verify user flows from onboarding to call review

### Subbu

- Set up staging and pilot production monitoring
- Add uptime checks
- Add deployment rollback notes
- Verify backup and restore process
- Create incident response checklist
- Confirm call recording consent policy before enabling recordings

### Exit Criteria

- Pilot property can be onboarded end to end
- Twilio number can handle inbound calls reliably
- Dashboard supports daily review workflow
- Sensitive questions and unknown answers escalate correctly
- Team can deploy, monitor, rollback, and debug

## Phase 6: Post-MVP Scale

Do not start this phase until the MVP pilot produces real usage feedback.

Possible work:

- PMS integrations
- Advanced reporting
- CRM sync
- Maintenance workflows
- Multilingual support
- Rescheduling
- Additional calendar providers
- More sophisticated lead scoring
- Automated leasing funnel analytics
