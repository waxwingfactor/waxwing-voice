"""
LLM (Large Language Model) provider adapters.

Stack decision (ADR-0006): Gemini-2.0 Flash via livekit-plugins-google is the
locked LLM provider. The VoicePipelineAgent handles all LLM calls internally;
the direct GeminiLLMAdapter (httpx REST) has been removed.

Remaining:
  - protocol.py  — LLMAdapter protocol + Message type
  - mock.py      — MockLLMAdapter for tests: deterministic, no network calls

MockLLMAdapter is retained because test_phase1_scenarios.py uses it to drive
VoiceSession conversation flows without a live LLM provider.
"""

from voice_agent.providers.llm.mock import MockLLMAdapter
from voice_agent.providers.llm.protocol import LLMAdapter, LLMProviderError, Message

__all__ = [
    "LLMAdapter",
    "LLMProviderError",
    "Message",
    "MockLLMAdapter",
]
