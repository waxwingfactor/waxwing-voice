"""
STT (Speech-to-Text) provider adapters.

Stack decision (ADR-0005): Deepgram STT is the locked provider for Waxwing Voice.
STT is now handled by the VoicePipelineAgent / livekit-plugins-deepgram integration.
The WhisperSTTAdapter has been removed as it is no longer used.

Remaining:
  - protocol.py  — STTAdapter protocol + TranscriptionEvent dataclass
  - mock.py      — MockSTTAdapter for tests: deterministic, no network calls

MockSTTAdapter is retained because test_phase1_scenarios.py uses it to drive
VoiceSession conversation flows without a live STT provider.
"""

from voice_agent.providers.stt.mock import MockSTTAdapter
from voice_agent.providers.stt.protocol import STTAdapter, STTProviderError, TranscriptionEvent

__all__ = [
    "STTAdapter",
    "STTProviderError",
    "TranscriptionEvent",
    "MockSTTAdapter",
]
