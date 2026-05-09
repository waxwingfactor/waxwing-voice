# Test 01: Prospect Asks About Rent and Availability

**Phase required:** Phase 1 (live voice loop)
**Tools exercised:** `get_property_profile`, `search_property_knowledge` (Phase 3)

## Setup

- Staging Twilio number is active
- Property "Maple Grove Apartments" is seeded with pricing data
- Call the staging Twilio number from a test phone

## Script

1. Agent greets: "Thanks for calling Maple Grove Apartments. How can I help you today?"
2. **Say:** "Hi, I'm looking for a one-bedroom apartment. What's the rent?"

**Expected:** Agent quotes rent from property knowledge — NOT an invented price.
If knowledge base has no pricing: agent says "I don't have current pricing details
in the information I can access. Let me have our leasing team follow up with you."

3. **Say:** "Do you have anything available in September?"

**Expected:** Agent uses availability data from property profile or says it cannot
confirm availability and offers to have the team follow up.

4. **Say:** "Great, can you tell me more about the community?"

**Expected:** Agent describes amenities from property profile only.

## Pass Criteria

- [ ] Agent never invents a rent price
- [ ] Agent uses only data from `get_property_profile` or retrieved knowledge
- [ ] If knowledge is missing, agent says so explicitly
- [ ] Agent offers a concrete next step (tour, follow-up, callback)
- [ ] Transcript segments appear in dashboard after call ends
