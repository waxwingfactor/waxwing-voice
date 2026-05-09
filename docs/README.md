# Waxwing Voice Documentation Index

Start here when onboarding or planning work.

## Core Documents

- [Detailed project document](00-project-document.md)
- [Phased implementation plan](01-phased-implementation-plan.md)
- [Team dependency map](02-team-dependency-map.md)
- [Tooling and guardrails](03-tooling-and-guardrails.md)
- [Folder structure and ownership](04-folder-structure-and-ownership.md)

## Owner Documents

- [Akhil: voice pipeline](team/akhil-voice-pipeline.md)
- [Harsha: backend](team/harsha-backend.md)
- [Alex: frontend](team/alex-frontend.md)
- [Subbu: DevOps and account setup](team/subbu-devops.md)

## MVP Locked Stack

- Telephony: Twilio
- Voice orchestration: LiveKit Agents
- STT: Whisper
- LLM: Gemini-3.0 Flash
- TTS: VibeVoice
- Frontend: Next.js, React, TypeScript
- Backend: Python 3.12, FastAPI, Uvicorn, Pydantic
- Python tooling: uv, Ruff, pytest
- Database: PostgreSQL with pgvector

## Primary Write Areas

- Alex: `apps/web`
- Harsha: `services/api`
- Akhil: `services/voice-agent`
- Subbu: `infra`
- Backend schemas: `services/api/app/schemas`
- OpenAPI/generated frontend types: `packages/shared/openapi` and `packages/shared/generated`
- Human-readable contracts: `docs/contracts`
