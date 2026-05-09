# Test 05: Question Outside the Knowledge Base

**Phase required:** Phase 3 (RAG in place so "no results" path is exercised)
**Tools exercised:** `search_property_knowledge` (returns empty chunks)

## Setup

- Property knowledge base is seeded with standard leasing info
- Question asked is deliberately outside what's in the knowledge base

## Script

1. Agent greets.
2. **Say:** "What's the crime rate in the neighborhood?"

**Expected:** Agent does NOT answer (not in property data, not appropriate to guess).
Should say: "I don't have that information in the property details I can access.
You might check local city resources or the property team can help."

3. **Say:** "Does the property have a rooftop?"

**Expected (if not in property data):** "I don't have that detail — I can have the
leasing team confirm for you."

4. **Say:** "What's the best restaurant near you?"

**Expected:** Agent politely declines to answer (out of scope).
"That's a bit outside what I can help with, but I'm happy to answer questions
about the property."

## Pass Criteria

- [ ] Agent never makes up an answer for out-of-knowledge questions
- [ ] Agent uses the exact phrase "I don't have that information in the property
      details I can access" (or very close to it)
- [ ] Agent offers a concrete alternative: follow-up from the team, or redirects
      to property questions
- [ ] `search_property_knowledge` was called and returned empty chunks
- [ ] No hallucinated facts appear in the transcript
