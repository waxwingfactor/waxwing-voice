# Akhil Project Document: Voice Pipeline

## 1. Mission

Akhil owns the real-time voice experience. The goal is to make a caller feel like they are speaking with a useful, accurate leasing assistant that can listen, answer, ask follow-up questions, and complete approved actions.

Primary scope:

- STT
- LLM orchestration
- TTS
- LiveKit Agent behavior
- Call state
- Voice prompts
- Voice tool usage
- Transcript and summary generation

## 2. Locked Tools

Use:

- Twilio for telephony
- LiveKit Agents for real-time voice orchestration
- Whisper for STT
- Gemini-3.0 Flash for the LLM
- VibeVoice for TTS
- Harsha's backend APIs for all durable business actions

Do not introduce:

- Another telephony provider
- Another voice orchestration framework
- Another STT provider
- Another LLM
- Another TTS provider
- Direct database writes from the voice service

## 3. Deliverables

### Phase 0

- Voice service skeleton
- Call state model draft
- Voice tool contract request list for Harsha
- Prompt structure draft for Gemini-3.0 Flash
- Local run instructions for the voice agent

### Phase 1

- Twilio inbound call reaches LiveKit Agent
- Caller speech is transcribed through Whisper
- Gemini-3.0 Flash generates responses
- VibeVoice speaks responses back to the caller
- Basic barge-in and silence handling
- Transcript segments emitted to backend
- Call lifecycle events emitted to backend

### Phase 2

- Lead qualification conversation flow
- Structured lead payloads
- Structured call summary payloads
- Low-confidence detection
- Human escalation trigger path
- Tool retry behavior for recoverable backend failures

### Phase 3

- RAG retrieval integration through Harsha's backend
- Property-specific answers grounded in retrieved knowledge
- Fallback language when property data is missing
- Source metadata in internal traces
- Test scripts for common property questions

### Phase 4

- Tour booking conversation flow
- Availability check tool usage
- Booking confirmation tool usage
- Follow-up email tool usage
- Handoff trigger for sensitive, failed, or uncertain flows

### Phase 5

- Latency tuning
- Voice smoke test suite
- Prompt guardrail refinement
- Failure mode documentation
- Pilot-ready call scripts

## 4. Backend Tools Needed From Harsha

Akhil should use these tools and should not create private alternatives:

- `search_property_knowledge`
- `get_property_profile`
- `create_or_update_lead`
- `create_call_event`
- `save_transcript_segment`
- `save_call_summary`
- `check_tour_availability`
- `book_tour`
- `send_follow_up_email`
- `request_human_handoff`

Each tool must have:

- Request schema
- Response schema
- Error codes
- Timeout expectations
- Idempotency guidance
- Example payload

## 5. Dependencies

Depends on Harsha for:

- API contracts
- Property profile endpoint
- RAG retrieval endpoint
- Lead and call persistence
- Booking and email tools
- Error model

Depends on Subbu for:

- Twilio number and routing
- LiveKit credentials
- Gemini credentials
- Whisper credentials or runtime
- VibeVoice credentials or runtime
- Staging deployment and logs

Depends on Alex for:

- Dashboard expectations for transcript, summary, call status, and lead fields
- Feedback on what call data must be readable by managers

## 6. Voice Behavior Requirements

The agent should:

- Greet callers clearly
- Identify intent quickly
- Ask only necessary follow-up questions
- Keep answers concise
- Confirm important details before actions
- Use approved property data
- Say when information is unavailable
- Escalate sensitive or uncertain questions
- End with a clear next step

The agent must not:

- Invent prices, availability, policies, fees, or guarantees
- Give legal or financial advice
- Make discriminatory statements
- Book a tour without confirming date, time, property, and caller details
- Send a follow-up email without confirming the email address
- Continue a failed workflow as if it succeeded

## 7. Prompt Guardrails

Prompts should include:

- Role: property leasing and resident support assistant
- Property context from backend
- Retrieved knowledge snippets, when available
- Known unavailable information
- Allowed actions
- Escalation rules
- Tone guidelines for phone calls
- Response length guidance
- Tool usage instructions

Prompts should not include:

- Secrets
- Raw credentials
- Internal implementation notes not needed by the model
- Large unfiltered documents
- Unscoped data from other properties

## 8. Test Scenarios

Minimum voice tests:

- New prospect asks about rent and availability
- Caller asks about pet policy
- Caller asks about parking
- Caller wants to book a tour
- Caller gives email with spelling correction
- Current resident asks about maintenance
- Caller asks a question not in the knowledge base
- Caller asks a sensitive Fair Housing or legal question
- Backend tool fails during booking
- Caller interrupts while the agent is speaking

## 9. Definition of Done

A voice feature is done when:

- It uses Twilio, LiveKit, Whisper, Gemini-3.0 Flash, and VibeVoice
- It calls backend tools for durable actions
- It handles success and failure paths
- It emits enough events for dashboard review
- It follows AI safety guardrails
- It has a repeatable manual test script
- It works in staging with Subbu's environment

