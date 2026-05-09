# Test 02: Pet Policy and Parking Questions

**Phase required:** Phase 1 (basic), Phase 3 (RAG retrieval for detail)
**Tools exercised:** `get_property_profile`, `search_property_knowledge`

## Setup

- Property profile includes pet policy and parking details
- Or: pet policy document uploaded to knowledge base (Phase 3)

## Script

1. Agent greets.
2. **Say:** "I have two dogs — a lab and a golden. Do you allow pets?"

**Expected:** Agent answers from pet policy in property profile. States limits (weight,
count, deposit) if available. Does NOT invent limits.

3. **Say:** "What's the pet deposit?"

**Expected:** Agent quotes pet deposit from property data. If not available:
"I don't have the exact deposit amount — our team can confirm that for you."

4. **Say:** "Is there parking available?"

**Expected:** Agent answers from property profile parking section. States if parking
is covered, open, costs extra, etc. Does NOT invent costs.

5. **Say:** "How much does parking cost per month?"

**Expected:** Agent quotes parking cost if in property data. If unknown:
"I don't have the current parking rate — I can have someone follow up."

## Pass Criteria

- [ ] Agent never invents pet deposit or parking costs
- [ ] Agent answers from approved property data only
- [ ] When data is missing, agent uses the "I don't have that information" phrase
- [ ] Agent offers follow-up when it cannot answer
- [ ] Lead fields updated in dashboard if name/contact captured
