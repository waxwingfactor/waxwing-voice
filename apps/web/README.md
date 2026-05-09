# Frontend App

Owner: Alex

This folder contains the Next.js dashboard for property managers. The first
prototype recreates the provided NAVA reference design with Waxwing Voice
branding.

## Local Commands

```bash
npm install
npm run dev -- --hostname 127.0.0.1 --port 3000
npm run lint
npm run build
```

The app scripts use webpack because Turbopack/PostCSS can hit local sandbox
process limits in this workspace.

## Environment

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

If `NEXT_PUBLIC_API_BASE_URL` is not set, the dashboard clearly shows API mock
mode and uses documented temporary mock data.

## Current Coverage

- Home dashboard with call, lead, tour, escalation, follow-up, and action-item KPIs
- Calls page with search and filters for date, property, intent, lead status, booked, follow-up, and escalated calls
- Call detail page with transcript, summary, caller info, extracted fields, booking, email status, action items, and handoff state
- Leads page with contact, move-in, budget, unit preference, tour status, and last call summary
- Property Knowledge page with structured facts, upload UI, document processing states, health indicators, re-index, and delete
- Settings page with business hours, escalation contacts, calendar status, email templates, voice settings, and property rules
- Loading, empty, error, and denied state previews for every page

Write here for:

- Dashboard routes
- React components
- UI state
- Frontend API clients
- Frontend tests

Do not write here for:

- Backend business logic
- Database access
- Provider SDK calls for Twilio, LiveKit, Gemini, Whisper, VibeVoice, calendar, or email
- Secrets

Shared contracts should come from `packages/shared` or `docs/contracts`.
