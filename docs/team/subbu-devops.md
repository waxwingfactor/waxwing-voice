# Subbu Project Document: DevOps and Account Setup

## 1. Mission

Subbu owns the operational foundation for Waxwing Voice. The goal is to make the team able to build, test, deploy, monitor, and safely operate the MVP without personal-only accounts, missing secrets, or unclear environments.

Primary scope:

- Provider accounts
- Secrets
- Environment variables
- Local and staging setup
- Deployment
- CI/CD
- Monitoring
- Backups
- Runbooks
- Incident response

## 2. Locked Tools and Providers

Set up and support:

- Twilio for telephony
- LiveKit for voice orchestration
- Gemini API access for Gemini-3.0 Flash
- Whisper credentials or runtime access
- VibeVoice credentials or runtime access
- Python 3.12 runtime support for the backend and voice services
- uv support for Python dependency installation
- PostgreSQL with pgvector
- S3-compatible object storage
- One transactional email provider selected in Phase 0
- Google Calendar OAuth first

Do not introduce:

- Alternative telephony providers
- Alternative LLM, STT, or TTS providers
- Multiple email providers during MVP
- Multiple databases
- Complex orchestration that the team cannot operate during pilot

## 3. Deliverables

### Phase 0

- Twilio account and test phone number
- Twilio SIP or approved voice bridge plan
- LiveKit environment
- Gemini API key or service credential
- Whisper credential or runtime path
- VibeVoice credential or runtime path
- Development PostgreSQL with pgvector
- Python 3.12 and uv setup notes for backend development
- Email provider decision
- Initial `.env.example` with Harsha and Akhil
- Account ownership notes

### Phase 1

- Twilio routing to LiveKit
- Staging LiveKit credentials
- Backend and voice service secret setup
- Basic service logs
- Test call runbook
- First staging deployment path

### Phase 2

- CI checks for frontend and backend
- Staging database backup or export plan
- Clean local setup verification
- Deployment notes for frontend, backend, and voice services

### Phase 3

- Object storage bucket
- Storage credentials
- Storage CORS configuration where needed
- Background job runtime
- pgvector verification in staging
- Document processing failure logs

### Phase 4

- Google Calendar OAuth app
- Calendar redirect URLs
- Email sender domain setup
- SPF, DKIM, and DMARC setup
- Provider webhook URLs, if needed
- Credential rotation notes

### Phase 5

- Staging and pilot production monitoring
- Uptime checks
- Deployment rollback notes
- Backup and restore test
- Incident response checklist
- Call recording consent and storage policy confirmation before enabling recordings

## 4. Environment Strategy

Use three environments:

- Local: developer machines with local or shared development services
- Staging: shared integration environment for team testing
- Pilot production: limited customer pilot environment

Each environment needs separate credentials for:

- Twilio
- LiveKit
- Gemini
- Whisper
- VibeVoice
- Database
- Object storage
- Email
- Calendar OAuth

## 5. Required Environment Variables

Subbu should maintain `.env.example` with Harsha and Akhil. Expected categories:

```text
APP_ENV=
WEB_PUBLIC_API_BASE_URL=

API_PORT=
DATABASE_URL=
OBJECT_STORAGE_ENDPOINT=
OBJECT_STORAGE_BUCKET=
OBJECT_STORAGE_ACCESS_KEY_ID=
OBJECT_STORAGE_SECRET_ACCESS_KEY=

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_SIP_TRUNK_ID=

LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

GEMINI_API_KEY=
WHISPER_API_KEY=
VIBEVOICE_API_KEY=

EMAIL_PROVIDER=
EMAIL_API_KEY=
EMAIL_FROM_ADDRESS=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
```

Only variables that are actually used should remain in the final `.env.example`.

## 6. Secrets Guardrails

- Never commit secrets.
- Do not paste secrets into chat, tickets, docs, or screenshots.
- Use separate secrets per environment.
- Rotate any secret that was exposed.
- Keep provider accounts under team or company ownership.
- Use least-privilege credentials where available.
- Document secret names, not secret values.

## 7. Deployment Requirements

Each service needs:

- Build command
- Start command
- Port
- Health check path
- Required environment variables
- Log format expectations
- Rollback steps

Services:

- Frontend web app
- Backend API
- Voice agent
- Background worker, if separate

## 8. Monitoring Requirements

Before pilot production, monitor:

- Frontend uptime
- Backend uptime
- Voice service uptime
- Database connectivity
- Twilio inbound call failures
- LiveKit room failures
- STT failures
- LLM failures
- TTS failures
- Email delivery failures
- Calendar booking failures
- Document processing failures

Logs should include:

- Request ID or call ID
- Service name
- Environment
- Error code
- Provider name
- Non-sensitive context

Logs should not include:

- API keys
- OAuth tokens
- Full unredacted PII payloads
- Full call transcripts unless intentionally stored in the database

## 9. Dependencies

Depends on Akhil for:

- Voice service runtime needs
- Twilio and LiveKit routing test requirements
- STT, LLM, and TTS env var usage
- Voice service health check

Depends on Harsha for:

- Database migration process
- Backend env var list
- Object storage needs
- Background job needs
- Email and calendar callback URLs

Depends on Alex for:

- Frontend env var list
- Staging domain needs
- OAuth redirect paths used by the browser

## 10. Definition of Done

A DevOps task is done when:

- Account ownership is documented
- Secrets are stored safely
- `.env.example` is updated if needed
- Local or staging setup is repeatable
- The service can be deployed
- Logs are available
- Rollback or recovery path is documented for pilot-critical systems
- No provider is added outside the approved stack
