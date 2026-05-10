"""
LLM adapter tests — MockLLMAdapter only.

GeminiLLMAdapter (httpx REST) has been removed; LLM is now handled by
VoicePipelineAgent via livekit-plugins-google (ADR-0006). Only MockLLMAdapter
is retained for test injection in VoiceSession scenario tests.

Manual test scenarios covered here:
  §5 Out-of-scope question → fallback (LLM returns safe response)
  §8 Caller interrupts AI → barge-in: LLM cancelled mid-response
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from voice_agent.providers.llm.mock import MockLLMAdapter
from voice_agent.providers.llm.protocol import LLMAdapter, LLMProviderError, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = "You are a leasing assistant for Maple Grove Apartments."
_HISTORY: list[Message] = [
    {"role": "user", "content": "What's the rent for a one-bedroom?"},
]


async def _drain(ait: AsyncIterator[str]) -> str:
    """Accumulate all tokens into a single string."""
    return "".join([t async for t in ait])


# ---------------------------------------------------------------------------
# 1. MockLLMAdapter — protocol compliance
# ---------------------------------------------------------------------------


class TestMockLLMAdapterProtocol:
    def test_is_llm_adapter_protocol(self) -> None:
        adapter = MockLLMAdapter()
        assert isinstance(adapter, LLMAdapter)

    def test_has_respond_streaming(self) -> None:
        adapter = MockLLMAdapter()
        assert callable(adapter.respond_streaming)

    def test_has_cancel(self) -> None:
        adapter = MockLLMAdapter()
        assert callable(adapter.cancel)


# ---------------------------------------------------------------------------
# 2. MockLLMAdapter — deterministic behaviour
# ---------------------------------------------------------------------------


class TestMockLLMAdapterBehaviour:
    @pytest.mark.asyncio
    async def test_single_response_yielded_as_tokens(self) -> None:
        adapter = MockLLMAdapter(responses=["Our units start at $1,800."])
        full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY))
        assert full == "Our units start at $1,800."

    @pytest.mark.asyncio
    async def test_multi_response_consumed_in_order(self) -> None:
        adapter = MockLLMAdapter(responses=["First answer.", "Second answer.", "Third answer."])
        for expected in ["First answer.", "Second answer.", "Third answer."]:
            full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY))
            assert full == expected

    @pytest.mark.asyncio
    async def test_tracks_respond_call_count(self) -> None:
        adapter = MockLLMAdapter(responses=["a", "b"])
        await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert adapter.respond_call_count == 2

    @pytest.mark.asyncio
    async def test_records_last_system_prompt(self) -> None:
        adapter = MockLLMAdapter(responses=["ok"])
        await _drain(adapter.respond_streaming("Custom system prompt", []))
        assert adapter.last_system_prompt == "Custom system prompt"

    @pytest.mark.asyncio
    async def test_records_last_history(self) -> None:
        adapter = MockLLMAdapter(responses=["ok"])
        history: list[Message] = [{"role": "user", "content": "Hello"}]
        await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, history))
        assert adapter.last_history == history

    @pytest.mark.asyncio
    async def test_fail_loud_when_responses_exhausted(self) -> None:
        adapter = MockLLMAdapter(responses=["only one"])
        await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        with pytest.raises(AssertionError, match="only 1 response"):
            await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))

    @pytest.mark.asyncio
    async def test_empty_responses_raises_on_first_call(self) -> None:
        adapter = MockLLMAdapter(responses=None)
        with pytest.raises(AssertionError, match="only 0 response"):
            await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))

    @pytest.mark.asyncio
    async def test_cancel_stops_token_stream(self) -> None:
        adapter = MockLLMAdapter(responses=["one two three four five"])
        tokens = []
        async for token in adapter.respond_streaming(_SYSTEM_PROMPT, []):
            tokens.append(token)
            if len(tokens) == 1:
                await adapter.cancel()
        # Should have stopped before all 5 words
        assert len(tokens) < 5

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        adapter = MockLLMAdapter()
        await adapter.cancel()
        await adapter.cancel()
        assert adapter.cancel_call_count == 2

    @pytest.mark.asyncio
    async def test_cancel_resets_for_next_call(self) -> None:
        adapter = MockLLMAdapter(responses=["first", "second"])
        # Cancel the first call
        await adapter.cancel()
        first = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        # Cancelled — may yield nothing or full (depending on cancel timing)
        # Second call: should work normally
        second = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert second == "second"

    @pytest.mark.asyncio
    async def test_fail_on_next_raises_llm_provider_error(self) -> None:
        adapter = MockLLMAdapter(responses=[], fail_on_next=True, fail_retryable=False)
        with pytest.raises(LLMProviderError) as exc_info:
            await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_fail_on_next_retryable(self) -> None:
        adapter = MockLLMAdapter(responses=["ok"], fail_on_next=True, fail_retryable=True)
        with pytest.raises(LLMProviderError) as exc_info:
            await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_fail_flag_consumed_once(self) -> None:
        adapter = MockLLMAdapter(responses=["good"], fail_on_next=True)
        with pytest.raises(LLMProviderError):
            await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        # Second call: flag consumed, should yield normally
        full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert full == "good"
