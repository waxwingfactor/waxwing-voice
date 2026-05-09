# Waxwing Voice Team Dependency Map

## 1. Purpose

This document explains when team members should depend on each other, what artifacts must be handed off, and which work should not proceed without another owner's contract.

The goal is to prevent hidden blockers, duplicate tooling, and integration surprises.

## 2. Ownership Summary

| Owner | Owns | Must not own |
| --- | --- | --- |
| Akhil | Voice agent, STT, Gemini-3.0 Flash orchestration, TTS, call state, voice prompts | Backend database schema, dashboard UI, cloud account ownership |
| Harsha | Backend API, database, RAG, workflow tools, integrations, validation | Frontend UI implementation, Twilio account setup, TTS/STT provider swapping |
| Alex | Frontend dashboard, user flows, UI states, API consumption | Direct database access, provider SDKs in browser, backend business rules |
| Subbu | Accounts, environments, secrets, deployment, monitoring, provider setup | Product business logic, UI implementation, prompt logic |

## 3. Dependency Rules

### 3.1 Akhil Depends On Harsha When

- The voice agent needs to save call records
- Transcript segments need durable storage
- The agent needs property profile data
- The agent needs RAG search
- The agent creates or updates a lead
- The agent checks availability or books a tour
- The agent sends an email or triggers handoff
- The agent needs validated schemas for tool calls

Required handoff from Harsha:

- Endpoint name or internal tool name
- Request schema
- Response schema
- Error model
- Authentication method
- Idempotency behavior for repeated tool calls
- Example payloads

### 3.2 Akhil Depends On Subbu When

- A Twilio number is needed for inbound testing
- LiveKit credentials are needed
- Gemini API credentials are needed
- Whisper credentials or runtime access is needed
- VibeVoice credentials or runtime access is needed
- Voice service deployment is needed
- Logs or traces are needed from staging

Required handoff from Subbu:

- Environment variable names
- Secret storage location
- Test phone number
- LiveKit room or project config
- Twilio routing notes
- Deployment command or pipeline link
- Observability dashboard link, when available

### 3.3 Harsha Depends On Akhil When

- Voice tool contracts need to reflect real call behavior
- Call event schemas need to support voice state
- Lead extraction fields need to match conversation flow
- RAG responses need metadata useful for prompts
- Summary payloads need to match generated output
- Error responses need to be useful for the agent

Required handoff from Akhil:

- Tool usage list
- Expected call lifecycle events
- Prompt-required fields
- Latency-sensitive endpoints
- Retry expectations
- Sample transcript and summary payloads

### 3.4 Harsha Depends On Alex When

- Dashboard API shape affects page usability
- Filters, sorting, and pagination are needed
- Settings screens need persistence
- Error and empty states need specific backend responses
- Upload progress and document statuses are displayed

Required handoff from Alex:

- Screen route
- Required fields per screen
- Filter and sort needs
- Empty, loading, and error state requirements
- Form validation expectations

### 3.5 Harsha Depends On Subbu When

- PostgreSQL and pgvector need provisioning
- Object storage bucket is needed
- Email provider credentials are needed
- Calendar OAuth credentials are needed
- Background jobs need a runtime
- API deployment and database migrations need staging support

Required handoff from Subbu:

- Database URL and migration policy
- Storage bucket and credentials
- Provider credentials
- OAuth redirect URLs
- Environment-specific secrets
- CI/CD deployment flow

### 3.6 Alex Depends On Harsha When

- Real API endpoints are needed for dashboard screens
- Upload endpoints are needed
- Settings endpoints are needed
- Auth or role behavior is introduced
- Filter, search, and pagination behavior must be implemented

Required handoff from Harsha:

- API schema
- Example responses
- Error responses
- Loading and processing status values
- Pagination model
- Mock data, if real endpoints are not ready

### 3.7 Alex Depends On Subbu When

- Frontend environment variables are needed
- Staging frontend deployment is needed
- OAuth redirect URLs affect browser flows
- Domain, SSL, or CORS configuration blocks testing

Required handoff from Subbu:

- Public environment variables
- Staging URL
- API base URL
- OAuth redirect URLs
- Deployment notes

### 3.8 Subbu Depends On Everyone When

- Environment variables are created or renamed
- Services need deployment scripts
- Provider webhook URLs are added
- Logs need structured fields
- A service needs scaling or health checks

Required handoff to Subbu:

- Service name
- Port
- Build command
- Start command
- Health check path
- Required environment variables
- External provider callbacks
- Expected logs and metrics

## 4. Phase-by-Phase Dependency Gates

### Phase 0 Gate

No one should build deep features until:

- Subbu lists required accounts and environment variables
- Harsha publishes initial API and data contract drafts
- Akhil publishes voice tool needs
- Alex publishes the first dashboard route map

### Phase 1 Gate

Voice foundation requires:

- Subbu: Twilio and LiveKit test route
- Akhil: live voice loop
- Harsha: call and transcript storage
- Alex: minimal call display

Blocking dependency:

- Akhil cannot prove inbound calls without Subbu's Twilio and LiveKit setup.
- Alex cannot display real calls without Harsha's call APIs.

### Phase 2 Gate

Backend and dashboard foundation requires:

- Harsha: stable calls, leads, summaries, properties APIs
- Akhil: structured lead and summary payloads
- Alex: dashboard screens consuming real APIs
- Subbu: CI and staging environment

Blocking dependency:

- Akhil should not invent private lead storage. Lead data must go through Harsha's backend.
- Alex should not hardcode fake response shapes after Harsha publishes schemas.

### Phase 3 Gate

Knowledge base requires:

- Harsha: upload, parsing, embeddings, retrieval
- Subbu: object storage and pgvector
- Alex: upload and status UI
- Akhil: RAG integration into the voice prompt

Blocking dependency:

- Akhil cannot claim grounded answers until Harsha's retrieval endpoint is connected.
- Alex cannot finish document UX without Harsha's processing statuses.

### Phase 4 Gate

Workflow automation requires:

- Harsha: calendar, booking, email, handoff endpoints
- Subbu: OAuth, email sender, provider setup
- Akhil: booking and email conversation paths
- Alex: UI for booking, email, and escalation status

Blocking dependency:

- Akhil cannot directly book calendar events from the voice service. Booking must go through Harsha's backend for auditability.
- Harsha cannot finish OAuth-backed integrations without Subbu's provider configuration.

### Phase 5 Gate

Pilot readiness requires:

- Akhil: tested voice scripts and known failure modes
- Harsha: authorization, workflow tests, retries
- Alex: polished dashboard flows
- Subbu: monitoring, backups, rollback, incident notes

Blocking dependency:

- Pilot production should not start until Subbu confirms monitoring and rollback.
- Call recording should stay disabled until consent policy is finalized.

## 5. Cross-Team Handoff Checklist

Use this checklist when one person hands work to another.

- What changed?
- Which owner needs to consume it?
- Is there a schema, endpoint, env var, or provider setting?
- Is there an example payload or screenshot?
- What are the known failure cases?
- How should it be tested?
- Is the change behind a feature flag or config?
- Does the change affect guardrails?
- Does it require an architecture decision record?

## 6. Daily Coordination Questions

Each person should answer these briefly during team sync:

- What integration point did I finish or change?
- Who depends on it?
- What is blocked by another owner?
- Did I introduce or rename any env var, endpoint, schema, or provider setting?
- Am I using only the approved MVP tools?

## 7. Escalation Rules

Escalate to the full team when:

- A locked provider appears unusable
- A new external tool is proposed
- A schema change breaks another owner
- Voice latency becomes unacceptable
- Twilio routing cannot reach LiveKit
- The agent produces unsafe or ungrounded answers
- Pilot deployment reliability is at risk

