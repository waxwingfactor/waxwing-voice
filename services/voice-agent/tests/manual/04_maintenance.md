# Test 04: Resident Maintenance Question

**Phase required:** Phase 1 (basic Q&A), Phase 3 (maintenance doc retrieval)
**Tools exercised:** `get_property_profile`, `search_property_knowledge`,
                    `create_call_event`

## Setup

- Property profile includes maintenance instructions
- Optional: maintenance policy document in knowledge base

## Script

1. Agent greets.
2. **Say:** "Hi, I'm a current resident. My kitchen faucet is leaking."
3. Agent should detect intent = resident_support / maintenance.

**Expected:** Agent gives maintenance intake instructions from property profile:
- Emergency line if urgent
- Resident portal or phone number for non-urgent requests
- Does NOT dispatch a vendor directly (out of scope for MVP)

4. **Say:** "Is this considered an emergency?"

**Expected:** Agent describes what qualifies as an emergency based on property policy.
If not in policy: "I'd recommend calling [emergency line] to check — they can determine
the urgency."

5. **Say:** "What's the maintenance phone number?"

**Expected:** Agent reads maintenance contact from property profile.

6. **Say:** "Can you schedule a repair for me?"

**Expected:** Agent explains it cannot schedule maintenance directly but gives the
correct contact method from property data. Does NOT promise a repair appointment.

## Pass Criteria

- [ ] Agent correctly identifies resident (not prospect) call type
- [ ] Agent uses maintenance instructions from property profile only
- [ ] Agent does not invent repair schedules or ETAs
- [ ] Emergency vs. non-urgent guidance follows property policy
- [ ] Call event `intent_detected` with intent=resident_support emitted to backend
- [ ] Dashboard shows call as resident_support type
