# Waxwing Voice Detailed Project Document

## 1. Purpose

Waxwing Voice is an AI voice agent platform for property management firms. The MVP will answer inbound leasing and resident calls, use property-approved knowledge to respond accurately, qualify leads, book tours, send follow-ups, and give property managers a dashboard with transcripts, summaries, action items, and lead records.

This document turns the original product brief into an implementation-ready project document for a four-person team:

- Akhil: voice pipeline, including STT, LLM, and TTS
- Harsha: backend, data model, APIs, RAG, workflows
- Alex: frontend dashboard and property manager experience
- Subbu: DevOps, account setup, secrets, environments, deployment

## 2. Locked MVP Platform Decisions

These decisions are part of the MVP guardrails. Do not replace them without a short written architecture decision record and team agreement.

| Area | MVP decision | Owner |
| --- | --- | --- |
| Telephony | Twilio | Subbu, Akhil |
| Real-time voice orchestration | LiveKit Agents | Akhil |
| STT | Whisper | Akhil |
| LLM | Gemini-3.0 Flash | Akhil |
| TTS | ElevenLabs Turbo v2.5 | Akhil |
| Frontend | Next.js, React, TypeScript | Alex |
| Backend API | Python, FastAPI | Harsha |
| Backend runtime | Uvicorn | Harsha, Subbu |
| Python dependency management | uv | Harsha, Subbu |
| Python linting and formatting | Ruff | Harsha |
| Python testing | pytest | Harsha |
| Database | PostgreSQL | Harsha, Subbu |
| Vector store | pgvector in PostgreSQL for MVP | Harsha |
| ORM and migrations | SQLAlchemy 2.x, Alembic | Harsha |
| API validation | Pydantic v2 | Harsha |
| Object storage | S3-compatible bucket | Subbu, Harsha |
| Email delivery | Resend | Harsha, Subbu |
| Calendar integrations | Google Calendar first, Outlook Calendar after MVP foundation is stable | Harsha, Subbu |

Architecture decisions that changed this table:
- [ADR-0001](adr/0001-elevenlabs-replaces-vibevoice.md) — ElevenLabs Turbo v2.5 replaces VibeVoice (TTS), 2026-05-09
- [ADR-0002](adr/0002-resend-replaces-sendgrid.md) — Resend replaces SendGrid (email), 2026-05-09
- [ADR-0003](adr/0003-local-deployment-for-mvp-demo.md) — Local deployment for MVP demo, 2026-05-09

## 3. Product Vision

Waxwing Voice should feel like a reliable virtual leasing assistant that knows every property, answers naturally, and completes useful work after the conversation.

The platform should help property managers:

- Capture missed, after-hours, and overflow leasing calls
- Reduce repetitive leasing and resident support work
- Provide accurate property-specific answers
- Book tours directly into a calendar
- Send personalized follow-up emails
- Store searchable call records
- Turn property documents into AI-usable knowledge
- Escalate uncertain or sensitive cases to a human

## 4. MVP Users

Primary users:

- Property managers
- Leasing teams
- Maintenance coordinators
- Prospective tenants
- Current residents

Initial customer profile:

- Small to mid-sized property management firms
- Multifamily apartment operators
- Leasing teams managing multiple properties
- Student housing operators
- Single-family rental management companies
- Build-to-rent communities

## 5. MVP Scope

The MVP includes:

- Inbound phone calls through Twilio
- LiveKit Agent for real-time call orchestration
- Whisper speech-to-text
- Gemini-3.0 Flash conversation and workflow reasoning
- ElevenLabs Turbo v2.5 text-to-speech
- Interruption handling, silence detection, and call state tracking
- Property manager dashboard
- Property profile setup
- Manual property details
- Document upload for property knowledge
- Document parsing, chunking, embeddings, and pgvector retrieval
- Call transcript storage
- AI-generated call summaries
- Lead capture and qualification
- Calendar booking for tours
- Follow-up email generation and delivery
- Basic human handoff and escalation rules
- Admin controls for business hours, property details, and contacts

Out of scope for the MVP:

- Full CRM replacement
- Native mobile app
- Advanced PMS integrations
- Payment processing
- Deep maintenance vendor dispatch
- Full multilingual support
- Resident portal replacement
- Automated legal, financial, or eligibility advice
- Multiple LLM, STT, TTS, or telephony provider experiments

## 6. Core Use Cases

### 6.1 Leasing Inquiry

A prospective tenant calls and asks about availability, rent, amenities, pet policy, location, parking, lease terms, application requirements, or move-in dates. The agent answers from approved property data, qualifies the lead, and offers to book a tour.

### 6.2 Tour Scheduling

The agent checks calendar availability, suggests open times, books the appointment, attaches lead details to the calendar event, and sends a confirmation email.

### 6.3 Lead Qualification

The agent captures:

- Name
- Phone number
- Email address
- Desired move-in date
- Budget
- Preferred bedroom count or unit type
- Pet information
- Occupancy needs
- Tour interest
- Urgency level
- Preferred contact method

### 6.4 Resident Support

Current residents can ask about rent payment instructions, maintenance procedures, office hours, policies, amenities, move-out steps, guest rules, parking rules, and escalation contacts.

### 6.5 Maintenance Intake

The agent gathers issue type, urgency, unit number, resident contact information, and a concise summary. For MVP, the system sends a notification or creates a basic internal record. Full vendor dispatch is out of scope.

### 6.6 Call Summary and Follow-Up

Every call produces:

- Transcript
- Summary
- Detected intent
- Extracted caller and lead fields
- Action items
- Lead score or priority indicator
- Suggested next step
- Escalation flag, if needed
- Related booking and email records

## 7. Target Architecture

### 7.1 Inbound Voice Flow

Caller -> Twilio phone number -> Twilio SIP trunk or approved Twilio voice bridge -> LiveKit room -> LiveKit Agent -> Whisper STT -> Gemini-3.0 Flash -> backend tools and RAG -> ElevenLabs Turbo v2.5 TTS -> LiveKit -> Twilio -> caller.

Subbu owns Twilio, LiveKit, and environment setup. Akhil owns the working voice agent and the voice pipeline behavior. If the team chooses a Twilio Media Streams bridge instead of SIP, Akhil and Subbu must document the reason because it changes latency, deployment, and operational behavior.

### 7.2 Dashboard Flow

Property manager -> Next.js dashboard -> backend API -> PostgreSQL and object storage.

The dashboard is not allowed to directly query the database or call external providers. Alex consumes backend APIs owned by Harsha.

### 7.3 Knowledge Base Flow

Property manager upload -> object storage -> backend document record -> parsing job -> chunks -> embeddings -> pgvector -> retrieval endpoint -> voice agent and dashboard preview.

Harsha owns the ingestion pipeline and retrieval API. Akhil consumes retrieval through backend tools. Alex displays document status, health, and editable property facts.

### 7.4 Workflow Automation Flow

Voice agent or dashboard action -> backend workflow endpoint -> database transaction -> external provider adapter -> audit log -> dashboard status update.

The voice agent must call backend tools for durable actions. It should not directly write business records to the database.

## 8. System Modules

Implementation folders:

- Alex owns `apps/web`
- Harsha owns `services/api`
- Akhil owns `services/voice-agent`
- Subbu owns `infra`
- Backend Pydantic schemas live in `services/api/app/schemas`
- OpenAPI and generated frontend types live in `packages/shared/openapi` and `packages/shared/generated`
- Human-readable contracts live in `docs/contracts`
- Architecture decisions live in `docs/adr`

### 8.1 Voice Agent Service

Owner: Akhil

Responsibilities:

- Receive LiveKit call sessions
- Maintain per-call conversation state
- Stream speech to Whisper
- Send grounded prompts to Gemini-3.0 Flash
- Call backend tools for retrieval and actions
- Convert responses through ElevenLabs Turbo v2.5
- Handle barge-in, pauses, silence, and call ending
- Produce transcript events and call lifecycle events

### 8.2 Backend API

Owner: Harsha

Responsibilities:

- Auth-ready API foundation
- FastAPI application structure
- Property, call, lead, booking, email, document, and audit models
- Voice tool endpoints
- Dashboard endpoints
- Calendar and email adapters
- RAG ingestion and retrieval
- Background jobs
- Validation, authorization, and durable writes

### 8.3 Frontend Dashboard

Owner: Alex

Responsibilities:

- Dashboard shell and navigation
- Home metrics view
- Call history and call detail
- Transcript and summary display
- Leads view
- Property knowledge view
- Document upload UI
- Settings for business hours, escalation contacts, calendar, email templates, and voice settings
- Loading, empty, error, and permission states

### 8.4 DevOps and Environments

Owner: Subbu

Responsibilities:

- Twilio account and phone numbers
- LiveKit project or server setup
- Gemini API access
- Whisper provider credentials
- ElevenLabs API key and voice selection
- Database and pgvector provisioning
- Object storage bucket
- Resend account, sender domain DNS setup (SPF/DKIM/DMARC)
- Calendar OAuth application setup
- Secret management
- Local, staging, and pilot production environments
- CI/CD and deployment runbooks
- Logging, monitoring, and incident checklist

## 9. Core Data Objects

### 9.1 Company

- id
- name
- billing status, if needed later
- created at
- updated at

### 9.2 User

- id
- company id
- name
- email
- role
- status
- created at
- updated at

### 9.3 Property

- id
- company id
- name
- address
- description
- amenities
- office hours
- leasing policies
- maintenance instructions
- escalation contacts
- business hour rules
- call handling rules
- created at
- updated at

### 9.4 Document

- id
- property id
- file name
- file type
- storage key
- upload status
- processing status
- extracted text reference
- chunk count
- last indexed at
- uploaded by
- created at
- updated at

### 9.5 Knowledge Chunk

- id
- document id
- property id
- chunk text
- source label
- page number, if available
- embedding vector
- created at

### 9.6 Call

- id
- property id
- Twilio call sid
- LiveKit room id
- caller phone number
- started at
- ended at
- duration
- transcript
- recording url, if enabled and consented
- summary
- primary intent
- sentiment
- escalation status
- status
- created at
- updated at

### 9.7 Lead

- id
- property id
- related call id
- name
- phone number
- email
- budget
- move-in date
- desired unit type
- pet information
- urgency
- tour interest
- lead score
- lead status
- created at
- updated at

### 9.8 Booking

- id
- lead id
- property id
- call id
- calendar provider
- calendar event id
- date
- time
- status
- confirmation email status
- created at
- updated at

### 9.9 Email

- id
- related call id
- related lead id
- property id
- recipient
- subject
- body
- delivery provider
- delivery status
- sent at
- created at
- updated at

### 9.10 Audit Log

- id
- actor type
- actor id
- action
- entity type
- entity id
- metadata
- created at

## 10. Voice Tool Contracts

The voice agent should use backend endpoints or internal service calls for business actions. Tool contracts must be versioned and validated.

Minimum voice tools:

- `search_property_knowledge(propertyId, query, callId)`
- `get_property_profile(propertyId)`
- `create_or_update_lead(propertyId, callId, leadFields)`
- `create_call_event(callId, eventType, payload)`
- `save_transcript_segment(callId, speaker, text, timestamp)`
- `save_call_summary(callId, summaryPayload)`
- `check_tour_availability(propertyId, dateRange)`
- `book_tour(propertyId, leadId, selectedSlot)`
- `send_follow_up_email(propertyId, leadId, templateType, context)`
- `request_human_handoff(propertyId, callId, reason)`

Harsha owns the server-side contracts. Akhil owns correct usage from the voice pipeline. Alex may display the resulting records but should not depend on private voice internals.

## 11. Dashboard Views

### 11.1 Home Dashboard

- Calls today
- New leads
- Tours booked
- Missed or escalated calls
- Follow-ups sent
- Open action items
- Recent call list

### 11.2 Call Detail

- Transcript
- Summary
- Caller information
- Intent
- Extracted lead details
- Suggested next steps
- Related emails
- Related booking
- Escalation reason, if present

### 11.3 Property Knowledge

- Property profile
- Structured facts
- Uploaded documents
- Processing status
- Last indexed timestamp
- Knowledge health indicators
- Re-index action

### 11.4 Leads

- Lead list
- Lead status
- Contact details
- Tour status
- Last call summary
- Follow-up history
- Filters by property, status, and date

### 11.5 Settings

- Business hours
- Escalation contacts
- Calendar connection
- Email templates
- Voice settings
- Property-specific rules

## 12. Security and Compliance Requirements

The MVP must include:

- Tenant-level data separation by company and property
- Role-aware API design
- Secure credential storage
- Encryption in transit
- Encrypted database and object storage where available
- Audit logs for document uploads, bookings, outbound emails, and settings changes
- PII minimization in logs
- Secure OAuth handling for calendar and email integrations
- Call recording disabled by default until consent language and local requirements are configured
- Human review for sensitive or uncertain cases

The agent must avoid legally sensitive claims about Fair Housing, protected classes, approval guarantees, eligibility, legal interpretation, or financial advice. When uncertain, it must escalate.

## 13. AI Behavior Guardrails

The AI voice agent must:

- Use approved property information for property-specific answers
- Say when information is unavailable
- Ask clarifying questions before making assumptions
- Confirm contact details before booking or sending emails
- Confirm date, time, property, and caller identity before booking
- Avoid inventing rent, availability, fees, policies, or guarantees
- Avoid discriminatory, legal, or financial advice
- Respect business hours and escalation settings
- Escalate emergencies, angry callers, sensitive legal topics, and low-confidence answers
- Keep answers concise in voice mode
- Store enough call state to recover from interruptions

## 14. MVP Success Metrics

Product metrics:

- Calls answered
- Missed calls reduced
- Leads captured
- Tours booked
- Follow-up emails sent
- Lead-to-tour conversion rate
- Escalation rate
- Property manager time saved

Technical metrics:

- Average voice response latency
- STT transcript quality
- TTS completion rate
- Tool call success rate
- RAG answer hit rate
- Calendar booking success rate
- Email delivery success rate
- Call completion rate
- Error rate by subsystem

## 15. MVP Acceptance Criteria

The MVP is ready for a pilot when:

- A real phone number can receive an inbound call through Twilio
- The call reaches a LiveKit Agent
- The agent can listen, respond, and handle interruptions
- The agent uses Whisper, Gemini-3.0 Flash, and ElevenLabs Turbo v2.5
- The agent can answer from a sample property knowledge base
- The agent can capture lead details during a call
- The agent can save a transcript and summary
- The agent can book a tour on a connected calendar
- The agent can send a follow-up email
- The dashboard shows calls, leads, transcripts, summaries, bookings, and document status
- Admin settings control business hours and escalation contacts
- Sensitive or uncertain questions trigger escalation behavior
- Staging has working secrets, monitoring, and deployment steps
