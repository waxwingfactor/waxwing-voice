# ADR-0002: Resend Replaces SendGrid as the Locked Email Provider

Date: 2026-05-09
Status: Accepted

## Context

Phase 0 deferred email provider selection to Subbu. Phase 4 (Harsha commit `15254af`) wired SendGrid as the transactional email provider via `httpx` in `services/api/app/integrations/email.py`. Since that commit the team has evaluated alternatives and decided to standardize on Resend.

The email provider powers one Phase 4 voice-agent action: `send_follow_up_email` (tour confirmations and general leasing follow-ups). At MVP scale this is low-volume transactional email — tens to low hundreds of messages per day. Deliverability infrastructure (SPF, DKIM, DMARC) and developer experience matter more than raw send volume pricing.

## Options

**Option 1: Keep SendGrid.**
Already wired in Harsha's backend. Large network, high deliverability, well-known. However: SendGrid's DX has regressed since the Twilio acquisition — documentation is fragmented, the Python SDK is heavyweight, and sender-domain authentication setup involves more steps than alternatives. Free tier is 100 emails/day; paid tiers jump sharply. Not a compelling fit for a lean MVP team.

**Option 2: Resend.**
Modern REST-first email API with a clean Python SDK (`resend` package). Generous free tier (3,000 emails/month). SPF/DKIM/DMARC setup is a guided single-domain flow — significantly simpler than SendGrid's domain authentication wizard. Deliverability is solid for transactional mail. The API is stable and well-documented. Best DX of the options considered.

**Option 3: Postmark.**
Excellent deliverability and strong transactional reputation. Strict transactional-only policy (no marketing email). Premium pricing — higher per-message cost than Resend for the same volume. No free tier beyond a small initial credit. Justified for high-stakes transactional mail (e.g., billing), but overkill for MVP leasing follow-ups.

**Option 4: Mailgun.**
Flexible API, good deliverability. Pricing and DX are comparable to SendGrid. No material advantage over Resend for this use case. Documentation quality is uneven.

## Decision

**Resend.**

Reasons:

1. **Best DX for the team's velocity.** Resend's API is the cleanest of the options, with first-class Python support and minimal boilerplate.
2. **Simpler sender-domain setup.** A single SPF/DKIM/DMARC configuration flow means Subbu can set up verified sending in under an hour. Misconfigured sender domains cause deliverability failures that are painful to debug in an MVP pilot.
3. **Sufficient deliverability for MVP scale.** Pilot volumes (tens of emails per day) are well within Resend's deliverability track record for transactional mail.
4. **Better pricing curve for early growth.** 3,000 emails/month free; paid tiers scale incrementally. SendGrid's pricing curve is steeper in the 1k–10k range.

## Consequences

**Backend (Harsha's scope):**
- `services/api/app/integrations/email.py` swaps the SendGrid client for the `resend` Python SDK.
- `resend` package added to `services/api/pyproject.toml`; `sendgrid` package removed.
- New environment variables: `RESEND_API_KEY`, `RESEND_FROM_EMAIL`.
- The email integration is already behind an adapter pattern in Harsha's codebase — the swap is a drop-in replacement at the adapter layer.

**Voice agent (Akhil's scope):**
- No code changes required. The voice agent calls `send_follow_up_email` via Harsha's backend tool endpoint. The underlying email provider is invisible to the voice agent.

**Operational:**
- Subbu provisions the Resend account, verifies the sending domain, and provisions `RESEND_API_KEY` and `RESEND_FROM_EMAIL` in all environments.
- SendGrid account and credentials should be decommissioned after migration is verified in staging.

**Documentation updates required:**
- `docs/00-project-document.md` §2 — Email Delivery row updated from "One transactional provider selected by Subbu in Phase 0" to "Resend".
- `docs/03-tooling-and-guardrails.md` §2 — Email row updated. "Do not introduce" column updated to list SendGrid and Mailgun explicitly.

## Rollback Plan

The email adapter pattern in Harsha's backend isolates the provider. Rolling back to SendGrid (or switching to another provider) requires:

1. Re-add the SendGrid SDK to `services/api/pyproject.toml`.
2. Revert the adapter implementation in `services/api/app/integrations/email.py`.
3. Swap `RESEND_API_KEY`/`RESEND_FROM_EMAIL` env vars for `SENDGRID_API_KEY`/`SENDGRID_FROM_EMAIL`.

Estimated cost: approximately half a day of Harsha's time.

## Impacted Owners

- **Harsha:** Code change in `services/api/app/integrations/email.py`. Dependency update. (Write zone: `services/api/`)
- **Subbu:** Resend account creation, sender domain DNS setup (SPF/DKIM/DMARC), `RESEND_API_KEY` and `RESEND_FROM_EMAIL` provisioning in all environments.
- **Akhil:** No code changes. This ADR is filed for traceability because the email provider decision affects the Phase 4 voice-agent `send_follow_up_email` tool contract (provider error codes may differ from SendGrid). Akhil will update the error handling in `BackendClient` if Resend surfaces different 4xx/5xx shapes.
- **Alex:** No code changes. Email delivery status is surfaced via backend API; the provider is invisible to the frontend.
