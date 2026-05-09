# Test 03: Tour Booking with Email Spelling Correction

**Phase required:** Phase 4
**Tools exercised:** `create_or_update_lead`, `check_tour_availability`,
                    `book_tour`, `send_follow_up_email`, `create_call_event`

## Setup

- Calendar connected in staging (Subbu)
- Property has available tour slots in next 2 weeks
- Email provider configured (Subbu)

## Script

1. Agent greets.
2. **Say:** "I'd like to schedule a tour."
3. Agent asks for preferred dates.
4. **Say:** "How about sometime next week?"
5. Agent reads back 2-3 available slots.
6. **Say:** "The Tuesday at 10 AM works."
7. Agent confirms: "I have Tuesday [date] at 10 AM at Maple Grove. Can I get your name?"
8. **Say:** "Jordan Lee."
9. Agent: "And a phone number?"
10. **Say:** "512-555-0199."
11. Agent: "Great. What email should I send the confirmation to?"
12. **Say:** "It's jay el ee at gmail dot com." (simulate phonetic spelling)
13. Agent reads back: "Let me confirm — j-l-e at gmail dot com — is that right?"
14. **Say:** "No, it's j-o-r-d-a-n dot l-e-e at gmail dot com."
15. Agent reads back: "jordan.lee at gmail dot com — is that correct?"
16. **Say:** "Yes."
17. Agent books and confirms: "You're all set! Tour booked for Tuesday [date] at 10 AM.
    Confirmation sent to jordan.lee@gmail.com."

## Pass Criteria

- [ ] Agent presents at most 3 slots (not the full list)
- [ ] Agent confirms date, time, property name, and caller name before calling `book_tour`
- [ ] Agent confirms email address by reading it back before calling `send_follow_up_email`
- [ ] Agent accepts spelling correction and re-confirms before proceeding
- [ ] `book_tour` is called exactly once (no double-booking)
- [ ] `send_follow_up_email` called only after email_confirmed = true
- [ ] Booking record visible in dashboard
- [ ] Confirmation email received at jordan.lee@gmail.com (check in staging)
- [ ] If `book_tour` fails: agent says booking did NOT complete, offers follow-up
