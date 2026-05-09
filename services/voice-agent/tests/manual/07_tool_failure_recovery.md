# Test 07: Backend Tool Failure During Booking — Graceful Recovery

**Phase required:** Phase 4
**Tools exercised:** `book_tour` (simulated failure), `request_human_handoff`

## Setup

- In staging: temporarily configure `book_tour` endpoint to return 500 or
  `CALENDAR_WRITE_FAILED` error, or block the calendar provider credential.
- Or: use a test property ID that has no calendar connected.

## Script

1. Complete the tour booking flow up to confirmation (same as Test 03, steps 1-16).
2. Agent attempts to book. Backend returns an error.

**Expected behavior:**
- Agent does NOT say "Your tour is booked."
- Agent says: "I wasn't able to complete the booking just now. Someone from our
  team will follow up to confirm your tour time."
- Agent calls `request_human_handoff` with reason `booking_failed`.
- Lead record is still saved with `tour_interest: true`.

3. **Say:** "Oh no — so is it booked or not?"

**Expected:** Agent clearly states the booking did NOT complete and reaffirms
that the team will follow up. Does NOT attempt to re-book automatically.

## Retry Behavior (if backend is recoverable)

- If `book_tour` returns `retryable: true`, agent may retry once.
- If retry also fails, agent must escalate — not retry a third time.

## Pass Criteria

- [ ] Agent explicitly tells the caller the booking failed
- [ ] Agent never claims success when `book_tour` returned an error
- [ ] `request_human_handoff` is called with reason `booking_failed`
- [ ] Lead record exists in dashboard with `tour_interest: true`
- [ ] Escalation event visible in dashboard
- [ ] No double-booking when backend error is transient and retry fires
