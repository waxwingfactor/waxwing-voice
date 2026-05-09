"""
TTS provider adapters.

All TTS providers must implement the TTSAdapter protocol defined in protocol.py.
The locked provider for Waxwing Voice MVP is ElevenLabs Turbo v2.5 (ADR-0001).

Exported for convenience:
    TTSAdapter          — Protocol (type-check and dependency injection)
    ElevenLabsTTSAdapter — Locked production adapter
    MockTTSAdapter      — Deterministic adapter for tests (no network)
"""

from voice_agent.providers.tts.elevenlabs import ElevenLabsTTSAdapter
from voice_agent.providers.tts.mock import MockTTSAdapter
from voice_agent.providers.tts.protocol import TTSAdapter

__all__ = ["TTSAdapter", "ElevenLabsTTSAdapter", "MockTTSAdapter"]
