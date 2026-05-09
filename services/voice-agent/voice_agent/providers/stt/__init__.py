"""
STT (Speech-to-Text) provider adapters.

Stack decision: Whisper (hosted OpenAI API) is the locked STT provider for Waxwing Voice.

Adapter pattern mirrors voice_agent/providers/tts/:
  - protocol.py  — STTAdapter protocol + TranscriptionEvent dataclass
  - whisper.py   — WhisperSTTAdapter using OpenAI's hosted Whisper REST API via httpx
  - mock.py      — MockSTTAdapter for tests: deterministic, no network calls

Usage::

    from voice_agent.providers.stt import WhisperSTTAdapter, MockSTTAdapter, STTAdapter
"""

from voice_agent.providers.stt.mock import MockSTTAdapter
from voice_agent.providers.stt.protocol import STTAdapter, STTProviderError, TranscriptionEvent
from voice_agent.providers.stt.whisper import WhisperSTTAdapter

__all__ = [
    "STTAdapter",
    "STTProviderError",
    "TranscriptionEvent",
    "WhisperSTTAdapter",
    "MockSTTAdapter",
]
