# Test 08: Caller Interrupts Mid-Response (Barge-In)

**Phase required:** Phase 1 (barge-in / interruption handling)
**Components exercised:** LiveKit audio track management, TTS stream cancellation

## Setup

- Live voice loop is active (Phase 1)
- VibeVoice TTS is streaming audio back to the caller

## Script

1. Ask a question that produces a multi-sentence response:
   **Say:** "Can you tell me everything about the community?"

2. Agent begins a longer response. While the agent is mid-sentence:
   **Interrupt and say:** "Actually, just tell me about the gym."

**Expected behavior:**
- Agent stops speaking immediately (TTS stream is cancelled)
- Agent does NOT finish the interrupted sentence
- Agent correctly processes the new utterance "tell me about the gym"
- Agent responds with gym-specific information

3. Verify normal conversation resumes after the interrupt.

## Edge Cases to Test

**Scenario B — Multiple rapid interruptions:**
1. Agent starts speaking.
2. Interrupt once with "wait".
3. Interrupt again immediately with "never mind, go ahead."
4. Agent resumes or asks "Go ahead — what would you like to know?"

**Scenario C — Interrupt during TTS then go silent:**
1. Agent starts speaking.
2. Interrupt with a sound but no clear word.
3. Silence for 3+ seconds.
4. Agent should re-prompt: "Are you still there?"

## Pass Criteria

- [ ] TTS stream is cancelled within 500ms of detecting caller speech
- [ ] No audio bleed (agent audio stops cleanly, no garbled overlap)
- [ ] New utterance is fully transcribed and processed
- [ ] Agent response addresses the new question, not the interrupted one
- [ ] Transcript shows the interrupted agent segment as truncated
- [ ] Barge-in does not crash the session or leave the call in a broken state
- [ ] Multiple rapid barge-ins are handled without exceptions
