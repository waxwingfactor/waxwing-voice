"""
Entry point: python -m voice_agent

Phase 0: prints readiness banner and validates configuration.
Phase 1: will start the LiveKit Agent worker and Twilio webhook listener.
"""

import sys

from voice_agent.config import Settings, get_settings
from voice_agent.logging_config import configure_logging


def main() -> None:
    configure_logging()

    import structlog

    log = structlog.get_logger("voice_agent.main")

    settings: Settings = get_settings()

    log.info(
        "Waxwing Voice Agent ready",
        version="0.1.0",
        phase="0-skeleton",
        providers_wired=False,
        livekit_url=settings.livekit_url or "(not set — required for Phase 1)",
        backend_api_url=settings.backend_api_url,
        property_id=settings.default_property_id or "(not set — set per-call in Phase 1)",
    )

    print(
        "\n"
        "========================================\n"
        " Waxwing Voice Agent — Phase 0 Skeleton \n"
        "========================================\n"
        " No provider connections are live yet.\n"
        " Phase 1 will wire: Twilio -> LiveKit -> Whisper -> Gemini -> VibeVoice\n"
        "\n"
        " Run `pytest` to verify call-state model and prompt builder.\n"
        "========================================\n"
    )

    if settings.livekit_url is None:
        log.warning(
            "LIVEKIT_URL is not set. Agent cannot connect to LiveKit. "
            "Required for Phase 1."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
