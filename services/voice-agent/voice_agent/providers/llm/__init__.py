"""
LLM (Large Language Model) provider adapters.

Stack decision: Gemini-2.0 Flash (locked per docs/03-tooling-and-guardrails.md).
Direct REST via httpx — no google-generativeai SDK to keep dependencies lean.

Adapter pattern mirrors voice_agent/providers/tts/ and voice_agent/providers/stt/:
  - protocol.py  — LLMAdapter protocol + Message type
  - gemini.py    — GeminiLLMAdapter using Google Gemini REST API via httpx
  - mock.py      — MockLLMAdapter for tests: deterministic, no network calls

Usage::

    from voice_agent.providers.llm import GeminiLLMAdapter, MockLLMAdapter, LLMAdapter
"""

from voice_agent.providers.llm.gemini import GeminiLLMAdapter
from voice_agent.providers.llm.mock import MockLLMAdapter
from voice_agent.providers.llm.protocol import LLMAdapter, LLMProviderError, Message

__all__ = [
    "LLMAdapter",
    "LLMProviderError",
    "Message",
    "GeminiLLMAdapter",
    "MockLLMAdapter",
]
