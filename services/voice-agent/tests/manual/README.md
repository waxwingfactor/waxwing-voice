# Manual Voice Test Scripts

These scripts cover the required test scenarios from `docs/team/akhil-voice-pipeline.md`.
Each is a step-by-step call script usable in Phase 1+ once the live voice loop is wired.

Run prerequisite: staging must be running with a live Twilio number.
See `services/voice-agent/README.md` for setup.

## Test Scenario Index

| # | Scenario | File |
|---|----------|------|
| 1 | Prospect asks rent / availability | `01_rent_availability.md` |
| 2 | Pet policy and parking questions | `02_pet_parking.md` |
| 3 | Tour booking with email spelling correction | `03_tour_booking.md` |
| 4 | Resident maintenance question | `04_maintenance.md` |
| 5 | Question outside knowledge base | `05_unknown_question.md` |
| 6 | Fair Housing / legal question (must escalate) | `06_fair_housing_escalation.md` |
| 7 | Backend tool failure during booking | `07_tool_failure_recovery.md` |
| 8 | Caller interrupts mid-response (barge-in) | `08_barge_in.md` |
