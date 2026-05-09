"""
Entry point: python -m voice_agent

Phase 0: prints readiness banner and validates configuration.
Phase 1: starts the LiveKit Agents worker (Twilio → LiveKit bridge, ADR-0004).

Usage:
    python -m voice_agent            # start the LiveKit Agents worker
    python -m voice_agent --dev      # dev mode: banner only, no worker registration
    python -m voice_agent connect <room-url> <api-key> <api-secret>  # LiveKit dev sandbox
"""

from __future__ import annotations

import sys

from voice_agent.config import Settings, get_settings
from voice_agent.logging_config import configure_logging


def _print_banner(settings: Settings) -> None:
    """Print the startup banner with key config values."""
    tts = settings.tts_provider
    stt = getattr(settings, "stt_provider", "whisper")
    llm = getattr(settings, "llm_provider", "gemini")

    providers_line = f"STT={stt}  LLM={llm}  TTS={tts}"
    livekit_line = settings.livekit_url or "(not set — required for Phase 1)"

    print(
        "\n"
        "============================================\n"
        " Waxwing Voice Agent — Phase 1              \n"
        "============================================\n"
        f" Providers: {providers_line}\n"
        f" LiveKit:   {livekit_line}\n"
        f" Backend:   {settings.backend_api_url}\n"
        " Architecture: Twilio → LiveKit bridge (ADR-0004)\n"
        "\n"
        " To run with mocked providers (no API keys needed):\n"
        "   STT_PROVIDER=mock LLM_PROVIDER=mock TTS_PROVIDER=mock python -m voice_agent\n"
        "\n"
        " Run pytest to execute all unit tests.\n"
        "============================================\n"
    )


def main() -> None:
    configure_logging()

    import structlog

    log = structlog.get_logger("voice_agent.main")

    settings: Settings = get_settings()

    _print_banner(settings)

    log.info(
        "Waxwing Voice Agent starting",
        version="0.1.0",
        phase="1-livekit-worker",
        livekit_url=settings.livekit_url or "(not set)",
        backend_api_url=settings.backend_api_url,
        tts_provider=settings.tts_provider,
        stt_provider=getattr(settings, "stt_provider", "whisper"),
        llm_provider=getattr(settings, "llm_provider", "gemini"),
    )

    # Validate critical configuration
    missing = []
    if not settings.livekit_url:
        missing.append("LIVEKIT_URL")
    if not settings.livekit_api_key:
        missing.append("LIVEKIT_API_KEY")
    if not settings.livekit_api_secret:
        missing.append("LIVEKIT_API_SECRET")

    if missing:
        log.warning(
            "Missing LiveKit configuration — worker cannot connect to LiveKit. "
            "See services/voice-agent/.env.example.",
            missing_vars=missing,
        )

    if not settings.voice_agent_jwt:
        log.warning(
            "VOICE_AGENT_JWT is not set. Backend tool calls will fail with 401. "
            "See BLOCKERS.md §7 for minting instructions."
        )

    # Attempt to start the LiveKit Agents worker.
    # This registers the entrypoint with LiveKit's job dispatch system and blocks
    # until the process is killed or the worker is stopped.
    from voice_agent.worker.entrypoint import create_worker_options

    worker_options = create_worker_options()
    if worker_options is None:
        log.warning(
            "LiveKit Agents worker not started — livekit-agents package not installed. "
            "Install with: uv add livekit-agents>=0.10"
        )
        # In offline/test mode: exit cleanly so pytest can import this module.
        sys.exit(0)

    # livekit.agents.cli.run_app is the standard way to register a LiveKit Agents worker.
    # It sets up signal handlers, connects to LiveKit, and blocks until stopped.
    try:
        from livekit.agents import cli
        log.info("Worker: connecting to LiveKit...", livekit_url=settings.livekit_url)
        cli.run_app(worker_options)
    except ImportError:
        log.error(
            "livekit.agents.cli not available. Install livekit-agents>=0.10.",
        )
        sys.exit(1)
    except Exception as exc:
        log.error(
            "Worker: fatal error",
            error=str(exc)[:500],
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
