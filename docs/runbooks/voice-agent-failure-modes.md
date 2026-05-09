# Voice Agent Failure Modes

**Owner:** Akhil (voice pipeline)
**Last updated:** 2026-05-09
**Audience:** On-call engineer or Akhil debugging a production incident.

---

## How to use this document

1. Find the symptom in the call dashboard or logs.
2. Match it to a failure mode section below.
3. Follow the **Recovery action** steps.
4. If the failure is a new pattern not documented here, add it and notify Akhil.

Every failure mode lists:
- **Cause** — what went wrong
- **Code path** — where in the codebase it's handled
- **Observable signal** — log line, CallEvent type, or dashboard indicator
- **Agent behavior** — what the caller hears
- **Operator recovery** — what you do to fix it

---

## Failure detection signals

### Logs

All voice agent logs go through Python's `logging` module with structured `extra` dicts.
Key logger names:

| Logger | Where |
|---|---|
| `voice_agent.agent.session` | VoiceSession per-turn loop |
| `voice_agent.conversation.escalation` | EscalationDetector |
| `voice_agent.conversation.booking` | TourBookingCoordinator |
| `voice_agent.conversation.email_followup` | FollowUpEmailCoordinator |
| `voice_agent.conversation.retrieval` | RetrievalCoordinator |
| `voice_agent.tools.backend_client` | All backend HTTP calls |

Key log patterns to grep for:

```
escalation.emergency          — emergency keyword triggered
escalation.fair_housing       — Fair Housing keyword triggered
escalation.legal_financial    — legal/financial keyword triggered
escalation.tool_failures      — 3+ consecutive tool failures
booking.slot_unavailable      — slot taken between check and book
booking.failed                — book_tour failed after retries
booking.retry                 — transient failure, retrying
email_followup.failed         — send_follow_up_email failed after retries
email_followup.retry          — transient email failure, retrying
retrieval.backend_error       — RAG lookup failed
retrieval.event_emit_failed   — CallEvent emit failed (non-critical)
handle_caller_turn.escalated  — session entered ESCALATION phase
```

### CallEvent types (visible in dashboard)

| Event type | Meaning |
|---|---|
| `call_started` | Session bootstrapped successfully |
| `call_ended` | Call finished (normal or escalated) |
| `escalated` | request_human_handoff was called |
| `tour_booked` | book_tour succeeded |
| `email_sent` | send_follow_up_email succeeded |
| `tool_failed` | A backend tool call returned an error |
| `knowledge_retrieved` | RAG lookup completed (with result count) |

### Dashboard indicators

- Call marked `escalated = true` → human handoff was triggered
- Lead record exists with `tour_interest = true` but no `booking_id` → booking failed; team must follow up
- Call with no `call_ended` event after 15 minutes → LiveKit session may be stuck (Phase 1 issue; see §Provider failures)

---

## Failure modes

---

### Backend tool failures

#### Transient (5xx, timeout, connection error — `retryable=True`)

**Cause:** Backend API is overloaded, deployed mid-request, or a dependency (calendar, DB) is briefly unavailable.

**Code path:**
- `BackendClient._get()` / `._post()` catch `httpx.TimeoutException` and `httpx.RequestError`
- Both raise `BackendToolError(code="TIMEOUT" | "CONNECTION_ERROR", retryable=True)`
- Coordinators (`TourBookingCoordinator._execute_booking`, `FollowUpEmailCoordinator._send_email`) retry exactly once
- After one retry fails: escalate with `escalation_reason="booking_failed"` or `"email_send_failed"`

**Observable signal:**
```
booking.retry   {"code": "TIMEOUT", "attempt": 1}
booking.failed  {"code": "TIMEOUT", "retryable": true, "attempt": 2}
```
Dashboard: `tool_failed` event, call escalated.

**Agent behavior:** "I wasn't able to complete that just now. Someone from our team will follow up."

**Operator recovery:**
1. Check backend health (`GET /health` on `services/api`).
2. If backend is down, the call is already escalated — the leasing team will follow up.
3. Verify the lead record saved before the failure (leads are saved incrementally).
4. If the booking didn't complete: manually book in the CRM and reach out to the prospect.

---

#### Permanent (4xx with `retryable=False`)

**Cause:** Bad request shape, missing resource (PROPERTY_NOT_FOUND, LEAD_NOT_FOUND), or validation error.

**Code path:**
- `BackendClient._parse_error()` reads `{"error": {"code": "...", "retryable": false}}`
- Coordinator or VoiceSession catches `BackendToolError(retryable=False)`
- No retry. Escalate or re-prompt (for BOOKING_SLOT_UNAVAILABLE — see below).

**Observable signal:**
```
booking.failed  {"code": "PROPERTY_NOT_FOUND", "retryable": false, "attempt": 1}
```

**Agent behavior:** "I wasn't able to complete that. Our team will follow up."

**Operator recovery:**
1. Check the `code` field in the log. `PROPERTY_NOT_FOUND` means the property_id in Twilio webhook config is wrong — update it (Subbu).
2. `LEAD_NOT_FOUND` during booking means create_or_update_lead failed earlier in the call — check for an earlier `tool_failed` event.

---

#### X-Company-Id rejected (401)

**Cause:** The company UUID in the `X-Company-Id` header doesn't match any record, or the header was dropped.

**Code path:**
- `BackendClient._parse_error()` for a 401 response
- Raises `BackendToolError(code="UNAUTHORIZED", retryable=False)`

**Observable signal:**
```
POST /v1/calls/ → HTTP 401
```
`create_call` fails → VoiceSession raises → call never starts.

**Agent behavior:** Caller hears silence or a generic error (LiveKit disconnect). No agent greeting.

**Operator recovery:**
1. Verify `COMPANY_ID` env var matches the record in `services/api` (check with Harsha).
2. Redeploy voice agent with the correct company UUID (Subbu).
3. `X-Company-Id` is sent both in client default headers AND repeated per-request — both values must match.

---

#### 404 on a path (route missing)

**Cause:** Harsha changed or hasn't yet deployed an endpoint that the voice agent calls.

**Code path:**
- `BackendClient._parse_error()` for 404
- `retryable=False` (404 → `response.status_code >= 500` is False)

**Observable signal:**
```
POST /v1/voice/book-tour → HTTP 404
BackendToolError(code="UNEXPECTED_RESPONSE", retryable=False)
```

**Operator recovery:**
1. Check `voice_agent/tools/backend_client.py` path constants against current `services/api/app/api/` routes.
2. If the route genuinely moved, update the path constant (one-line change in `backend_client.py`).
3. Flag Harsha if the route doesn't exist yet.

---

### State machine corners

#### Caller refuses every confirmation

**Cause:** Caller keeps saying "no" at the confirmation gate for booking or email.

**Code path:**
- `TourBookingCoordinator._from_awaiting_confirmation()` parses `ConfirmationResult.REFUSED`
- Returns to `AWAITING_DATE_PREFERENCE` each time
- No maximum refusal limit in the coordinator — this loops indefinitely

**Observable signal:**
Repeated turns in `AWAITING_BOOKING_CONFIRMATION` in transcript. No `tour_booked` event.

**Agent behavior:** "No problem — what dates or times work better for you?" (repeats)

**Operator recovery:**
- Currently there is no hard refusal limit that triggers escalation.
- If this becomes a support pattern, add a `refusal_count` guard (flag for Akhil as Phase 5 follow-up).
- Manual override: leasing team can follow up directly if the call ends without booking.

---

#### Caller jumps topics mid-capture

**Cause:** Caller switches from "I want to schedule a tour" to "actually, what's the pet policy?"
State machine is in LEAD_CAPTURE but caller asks an information question.

**Code path:**
- `LeadCaptureStateMachine.advance()` re-evaluates intent on each turn
- RAG retrieval runs after the advance (`RetrievalCoordinator.do_retrieve()`)
- LeadFields captured so far are preserved (state machine never clears captured fields)

**Observable signal:** No error. Phase may stay in LEAD_CAPTURE or shift to KNOWLEDGE_RETRIEVAL.

**Agent behavior:** Agent answers the knowledge question then continues lead capture.

**Operator recovery:** No action needed. This is expected behavior.

---

#### Empty retrieval

**Cause:** `search_property_knowledge` returns 0 results for the caller's question.

**Code path:**
- `RetrievalCoordinator.do_retrieve()` filters by `similarity_score >= 0.5`
- If 0 chunks survive: `KnowledgeSlot(knowledge_unavailable=True)`
- `build_system_prompt()` renders the "KNOWLEDGE STATUS: No knowledge available" instruction
- Gemini is told to say "I don't have that information"

**Observable signal:**
```
retrieval.completed {"injected_count": 0, "knowledge_unavailable": true}
```
VoiceSession result: `knowledge_unavailable=True`.

**Agent behavior:** "I don't have that information in the property details I can access. I can have the leasing team follow up with you — would that work?"

**Operator recovery:**
1. If this happens for a question that should be answered: the property knowledge base needs more content.
2. Flag for whoever manages property data ingestion (Harsha / property manager).

---

#### Low confidence cascade

**Cause:** Gemini response contains heavy hedging language. `ConfidenceEvaluator` scores it below threshold (default 0.4).

**Code path:**
- `ConfidenceEvaluator.evaluate(llm_response)` returns `ConfidenceResult(is_low=True)`
- `VoiceSession.evaluate_llm_response()` triggers `EscalationReason.LOW_CONFIDENCE`
- `request_human_handoff()` is called

**Observable signal:**
```
handle_caller_turn.escalated {"reason": "low_confidence"}
```
Dashboard: call escalated with reason `low_confidence`.

**Agent behavior:** "I'm not confident I have the right information for you. Let me connect you with our team."

**Operator recovery:**
1. Review the transcript for what question triggered it.
2. If it's a legitimate question: add knowledge to the property knowledge base.
3. If the threshold is too aggressive: adjust `LOW_CONFIDENCE_THRESHOLD` in settings (coordinate with Akhil).

---

### Coordinator failures

#### TourBookingCoordinator: BOOKING_SLOT_UNAVAILABLE

**Cause:** Slot was available when checked but taken by another booking before `book_tour` was called (race condition).

**Code path:**
- `_execute_booking()` catches `BackendToolError(code="BOOKING_SLOT_UNAVAILABLE")`
- Re-enters `AWAITING_DATE_PREFERENCE` immediately — does not retry (slot is gone)
- `booking_confirmed` remains False

**Observable signal:**
```
booking.slot_unavailable {"code": "BOOKING_SLOT_UNAVAILABLE", "attempt": 1}
```

**Agent behavior:** "Unfortunately that slot was just taken. What other dates or times work for you?"

**Operator recovery:** No operator action needed — caller selects a new slot. If all slots disappear: leasing team follows up.

---

#### TourBookingCoordinator: repeated empty availability

**Cause:** `check_tour_availability` returns 0 slots for the requested window.

**Code path:**
- `_from_awaiting_date()`: `if not slots:` → `BOOKING_FAILED`, `escalate=True`
- `VoiceSession` triggers escalation

**Observable signal:**
```
booking.no_slots_available
```
Call escalated.

**Agent behavior:** "I don't see any open slots for that window. Let me have our leasing team reach out to find a time that works."

**Operator recovery:**
1. Check the calendar for that property — it may be fully booked.
2. Add availability or have the leasing team call back to book manually.

---

#### TourBookingCoordinator: retry exhausted

**Cause:** Transient backend failure persists across the retry attempt.

**Code path:**
- `_execute_booking()` retries once (`MAX_BOOKING_RETRIES = 1`)
- Second attempt also fails → `BOOKING_FAILED`, `escalate=True`, `done=True`

**Observable signal:**
```
booking.retry   {"attempt": 1}
booking.failed  {"attempt": 2}
```
Dashboard: call escalated, `tool_failed` event twice.

**Agent behavior:** "I wasn't able to complete the booking just now. Someone from our team will reach out to confirm your tour time."

**Operator recovery:**
1. The lead record should exist with `tour_interest=true`. Verify in dashboard.
2. Leasing team must manually book and confirm with the prospect.
3. Investigate backend health at the time of the incident.

---

#### FollowUpEmailCoordinator: send failure

**Cause:** `send_follow_up_email` fails (transient or permanent).

**Code path:**
- `_send_email()` retries once on transient (`MAX_EMAIL_RETRIES = 1`)
- On exhaustion or permanent failure: `EMAIL_FAILED`, `escalate=True`
- `email_sent` remains False

**Observable signal:**
```
email_followup.failed {"code": "...", "attempt": 2}
```

**Agent behavior:** "I wasn't able to send that email right now. Our team will follow up with the confirmation details."

**Operator recovery:**
1. Lead record has the email address. Send a manual confirmation email from the CRM.
2. Check email provider status (Subbu owns provider credentials).

---

#### FollowUpEmailCoordinator: email never confirmed

**Cause:** Caller hung up before confirming the email address, or kept refusing.

**Code path:**
- `FollowUpEmailCoordinator._send_email()` is only called when `email_confirmed=True`
- If call ends in `CONFIRMING_EMAIL` or `AWAITING_CORRECTION`: no email is sent

**Observable signal:**
No `email_sent` CallEvent. `email_confirmed=False` in call state.

**Agent behavior:** Call ends without sending email.

**Operator recovery:** Check the lead record. If email exists in lead fields, send manually.

---

#### RetrievalCoordinator: empty results

**Cause:** Vector search finds nothing relevant for the caller's question.

**Code path:** See "Empty retrieval" under State machine corners above.

---

#### RetrievalCoordinator: error from backend

**Cause:** `search_property_knowledge` returns an error (timeout, 5xx, connection failure).

**Code path:**
- `do_retrieve()` catches all exceptions
- Returns `KnowledgeSlot(knowledge_unavailable=True)`
- Never raises; never crashes the call

**Observable signal:**
```
retrieval.backend_error {"error": "...", "call_id": "..."}
```

**Agent behavior:** Falls back to "I don't have that information" response template.

**Operator recovery:**
1. Check vector DB / knowledge service health.
2. This is a degraded-mode: the call continues but agent cannot answer knowledge questions.
3. No operator action needed per-call — fix the underlying service.

---

### Provider failures (Phase 1 finish-up — documented for completeness)

These failures cannot be tested without live provider credentials (Subbu-blocked).
Behavior documented here is the intended design, not yet fully tested.

#### Whisper STT timeout / no transcription

**Cause:** Whisper model fails to return a transcription within timeout, or returns empty text.

**Code path:** `VoiceSession._stt_transcribe()` — currently raises `NotImplementedError` (Phase 1 stub).

**Intended behavior:**
- Empty transcription: treat as silence → emit `silence_detected` event → prompt caller to repeat.
- Timeout: log `stt.timeout`, treat as silence.

**Observable signal:** `silence_detected` CallEvent in dashboard.

**Operator recovery:** If silence events are frequent: check Whisper model health / latency (Subbu).

---

#### Gemini LLM timeout / refusal / contentless response

**Cause:** Gemini API unavailable, rate-limited, or returns an empty/refused response.

**Code path:** `VoiceSession._llm_respond()` — currently raises `NotImplementedError`.

**Intended behavior:**
- Timeout: use a canned safe response ("I'm having technical difficulties — let me connect you with our team.") and escalate.
- Content refusal: treat as low confidence → escalate with `LOW_CONFIDENCE` reason.
- Empty response: re-prompt once; if still empty, escalate.

**Operator recovery:** Check Gemini API status and quota (Subbu owns API keys).

---

#### VibeVoice TTS failure

**Cause:** TTS synthesis fails; audio cannot be sent to caller.

**Code path:** `VoiceSession._tts_speak()` — currently raises `NotImplementedError`.

**Intended behavior:** Log error, attempt to reconnect once. If still failing: end the LiveKit session gracefully (caller will hear a disconnect tone, not silence).

**Operator recovery:** Check VibeVoice service status (Subbu).

---

#### LiveKit room disconnect

**Cause:** Network interruption, room timeout, or LiveKit worker crash.

**Code path:** Phase 1 LiveKit event loop (not yet implemented).

**Intended behavior:** On unexpected disconnect, VoiceSession should flush transcript and save summary in a `finally` block, then emit `call_ended` with reason `disconnected`.

**Observable signal:** No `call_ended` event after expected call duration → room disconnected without clean shutdown.

**Operator recovery:**
1. Check LiveKit dashboard for room status.
2. Check whether the call record exists in the dashboard (may have transcript from before disconnect).
3. If the call had a lead or booking in progress: manual follow-up required.

---

#### Twilio webhook failure

**Cause:** Twilio cannot POST the inbound call webhook to the voice agent endpoint.

**Code path:** Twilio TwiML/webhook handler (owned by Subbu's infra layer).

**Observable signal:** No `call_started` event for an inbound call. Twilio dashboard shows webhook delivery failure.

**Operator recovery:**
1. Check the webhook URL configured in the Twilio console for the staging/production number.
2. Verify the voice agent server is reachable at that URL (Subbu).
3. Check Twilio error logs for the specific SID.

---

### AI safety violations

These are not runtime errors — they require human review of transcripts.

#### Agent invents information

**Symptom:** Agent states a rent amount, availability date, or policy detail that is NOT in the retrieved knowledge or property profile.

**Detection:** Manual transcript review. No automated detection yet.

**Expected safeguard:** The system prompt contains explicit rules (rule 1-4 in `_SAFETY_RULES_TEXT`). The `KnowledgeSlot` renders an "only use these facts" instruction. Gemini is instructed not to invent.

**Recovery:**
1. Pull the transcript and the property knowledge base snapshot for that call.
2. Identify which prompt rule failed.
3. Strengthen the relevant prompt rule (edit `system_prompt.py`).
4. If the invented information caused a real-world harm (wrong price quoted, wrong availability promised): contact the affected prospect and correct it (Akhil + leasing team).
5. Document the case in `BLOCKERS.md` as a known edge case.

---

#### Agent makes a Fair Housing assertion

**Symptom:** Agent attempts to answer a Fair Housing question instead of escalating.

**Expected safeguard:**
1. Code-side: `EscalationDetector.check()` runs on every caller turn BEFORE the LLM.
2. Prompt-side: `_ESCALATION_RULES_TEXT` instructs Gemini to self-escalate.

**If it happens:** The code-side detector should have caught it first. A bypass means either:
- The keyword was phrased in a way not covered by `FAIR_HOUSING_KEYWORDS` in `escalation.py`.
- The LLM was given a non-standard turn format that bypassed the detector.

**Recovery:**
1. Get the exact caller text from the transcript.
2. Add the missing keyword or phrase to `FAIR_HOUSING_KEYWORDS` in `escalation.py`.
3. Update the corresponding escalation trigger list in `_ESCALATION_RULES_TEXT` in `system_prompt.py`.
4. Run the escalation detector test suite to confirm the new keyword is caught.
5. Flag as a compliance incident and notify legal/compliance team.

---

#### Agent claims tool succeeded when it failed

**Symptom:** Agent says "Your tour is booked!" but `book_tour` returned an error.

**Expected safeguard:**
- `TourBookingCoordinator._execute_booking()`: booking_confirmed is only set to True in the success branch.
- `VoiceSession` only sets `state.booking_confirmed=True` when `booking_result.booking_confirmed=True`.
- System prompt rule: "Never claim a tour is booked unless book_tour returned a successful response."

**If it happens:** The LLM generated this claim despite the rule. The tool-call gate in VoiceSession should have prevented it — investigate whether the agent_prompt from the coordinator was exposed to Gemini in a way that bypassed the gate.

**Recovery:**
1. Pull transcript + logs for the call.
2. Check whether `book_tour` was actually called and what it returned.
3. If booking didn't happen: leasing team must manually book and apologize to the prospect.
4. Strengthen the defensive tool-call prompt rule and add a test case for this specific phrasing.

---

#### Agent commits to action without confirmation

**Symptom:** Agent books a tour or sends email without reading back confirmation details.

**Expected safeguard:**
- Booking: `TourBookingCoordinator` only calls `book_tour` after `AWAITING_BOOKING_CONFIRMATION` gate and `ConfirmationResult.GIVEN`.
- Email: `FollowUpEmailCoordinator` only calls `send_follow_up_email` after `email_confirmed=True`.
- Both are enforced in code, not just in the prompt.

**If it happens:** Check whether the coordinator states were bypassed (e.g., state machine jumped directly to ACTION phase without going through the coordinator).

---

## Recovery and escalation

For any incident:

1. **Preserve the call record.** Pull `call_id`, `backend_call_id`, transcript segments, and CallEvents from the dashboard before they age out.
2. **Check the lead record.** Even if the booking or email failed, the lead fields captured during the call should be in the database.
3. **Follow up with the prospect.** Leasing team must reach out manually if the call ended in a failure state.
4. **File the failure pattern.** Add new keywords or edge cases discovered to `escalation.py` and `system_prompt.py`.
5. **Notify Akhil** for anything that bypasses AI safety rules (Fair Housing, tool-success claims on failure).

---

## Open follow-ups (cross-reference BLOCKERS.md)

| Blocker | Impact |
|---|---|
| STT/LLM/TTS providers (Subbu-blocked) | Latency tuning, real audio barge-in, LLM behavior validation cannot be tested |
| OQ-12: Maintenance email flow | Maintenance calls end at handoff; no follow-up email sent |
| No refusal-count limit on booking confirmation loop | Caller can refuse indefinitely — leasing team must follow up |
| No automated Fair Housing assertion detection | Manual transcript review only; no alerting |
| Sentiment/distress auto-detect | Keyword-only; nuanced distress may be missed |
| JWT auth (X-Company-Id is temporary) | Phase 5 auth hardening not complete |

See `BLOCKERS.md` for full details and open questions for Harsha (OQ-1 through OQ-12).
