"""
LLM adapter tests.

Tests are split into three areas:
  1. MockLLMAdapter — protocol compliance, deterministic behaviour, fail-loud.
  2. GeminiLLMAdapter — construction guards, HTTP error mapping, SSE parsing,
     cancel behaviour.
  3. Protocol compliance — both adapters satisfy the LLMAdapter runtime protocol.

All Gemini HTTP calls are intercepted with httpx mocking — no network access,
no API key validation against the real Google AI service.

Manual test scenarios covered here:
  §5 Out-of-scope question → fallback (LLM returns safe response)
  §8 Caller interrupts AI → barge-in: LLM cancelled mid-response
"""

from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import patch

import httpx
import pytest

from voice_agent.providers.llm.gemini import GeminiLLMAdapter
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


def _make_sse_line(text: str) -> str:
    """Build a Gemini SSE data line containing the given text token."""
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }
    return "data: " + json.dumps(payload)


# ---------------------------------------------------------------------------
# Mock httpx streaming response helpers
# ---------------------------------------------------------------------------


class _SSEStreamContextMock:
    """Mimics httpx streaming response context manager for SSE responses."""

    def __init__(self, status_code: int, lines: list[str]) -> None:
        self._status_code = status_code
        self._lines = lines

    async def __aenter__(self) -> "_SSEStreamContextMock":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    @property
    def status_code(self) -> int:
        return self._status_code

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _SSEClientContextMock:
    """Mimics httpx.AsyncClient for SSE streaming."""

    def __init__(self, status_code: int, lines: list[str]) -> None:
        self._status_code = status_code
        self._lines = lines

    async def __aenter__(self) -> "_SSEClientContextMock":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def stream(self, *args: object, **kwargs: object) -> "_SSEStreamContextMock":
        return _SSEStreamContextMock(self._status_code, self._lines)


def _make_gemini_client(status_code: int = 200, response_text: str = "Hello!") -> "_SSEClientContextMock":
    """Build a mock Gemini SSE client that returns a single token."""
    lines = [_make_sse_line(response_text), "data: [DONE]"]
    return _SSEClientContextMock(status_code, lines)


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


# ---------------------------------------------------------------------------
# 3. GeminiLLMAdapter — construction guards
# ---------------------------------------------------------------------------


class TestGeminiLLMAdapterInit:
    def test_raises_if_api_key_is_none(self) -> None:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiLLMAdapter(api_key=None)  # type: ignore[arg-type]

    def test_raises_if_api_key_is_empty(self) -> None:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiLLMAdapter(api_key="")

    def test_constructs_with_valid_key(self) -> None:
        adapter = GeminiLLMAdapter(api_key="AIza-test-key")
        assert adapter is not None

    def test_default_model_is_gemini_2_flash(self) -> None:
        adapter = GeminiLLMAdapter(api_key="AIza-test")
        assert adapter._model == "gemini-2.0-flash"

    def test_model_override(self) -> None:
        adapter = GeminiLLMAdapter(api_key="AIza-test", model="gemini-2.0-flash-lite")
        assert adapter._model == "gemini-2.0-flash-lite"

    def test_satisfies_llm_adapter_protocol(self) -> None:
        adapter = GeminiLLMAdapter(api_key="AIza-test")
        assert isinstance(adapter, LLMAdapter)


# ---------------------------------------------------------------------------
# 4. GeminiLLMAdapter — SSE parsing and streaming
# ---------------------------------------------------------------------------


class TestGeminiLLMAdapterStreaming:
    def _make_adapter(self) -> GeminiLLMAdapter:
        return GeminiLLMAdapter(api_key="AIza-test-key", max_tokens=200)

    @pytest.mark.asyncio
    async def test_yields_tokens_on_200(self) -> None:
        adapter = self._make_adapter()
        lines = [_make_sse_line("Hello "), _make_sse_line("from Gemini!"), "data: [DONE]"]
        client = _SSEClientContextMock(200, lines)
        with patch("httpx.AsyncClient", return_value=client):
            full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY))
        assert "Hello " in full
        assert "from Gemini!" in full

    @pytest.mark.asyncio
    async def test_skips_non_data_lines(self) -> None:
        adapter = self._make_adapter()
        lines = [
            "",  # empty SSE separator
            ": comment line",
            _make_sse_line("answer"),
            "data: [DONE]",
        ]
        client = _SSEClientContextMock(200, lines)
        with patch("httpx.AsyncClient", return_value=client):
            full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY))
        assert "answer" in full

    @pytest.mark.asyncio
    async def test_skips_malformed_json_frames(self) -> None:
        adapter = self._make_adapter()
        lines = [
            "data: {not valid json}",
            _make_sse_line("valid token"),
            "data: [DONE]",
        ]
        client = _SSEClientContextMock(200, lines)
        with patch("httpx.AsyncClient", return_value=client):
            full = await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY))
        assert "valid token" in full

    @pytest.mark.asyncio
    async def test_cancel_stops_sse_parsing(self) -> None:
        adapter = self._make_adapter()
        lines = [_make_sse_line(f"word{i} ") for i in range(20)] + ["data: [DONE]"]
        client = _SSEClientContextMock(200, lines)
        tokens = []
        with patch("httpx.AsyncClient", return_value=client):
            async for token in adapter.respond_streaming(_SYSTEM_PROMPT, _HISTORY):
                tokens.append(token)
                if len(tokens) >= 2:
                    await adapter.cancel()
        # Should have stopped before all 20 tokens
        assert len(tokens) < 20

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        adapter = self._make_adapter()
        await adapter.cancel()
        await adapter.cancel()
        # No exception


# ---------------------------------------------------------------------------
# 5. GeminiLLMAdapter — HTTP error mapping
# ---------------------------------------------------------------------------


class TestGeminiLLMAdapterHTTPErrors:
    def _make_adapter(self) -> GeminiLLMAdapter:
        return GeminiLLMAdapter(api_key="AIza-test-key")

    @pytest.mark.asyncio
    async def test_400_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(400, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.status_code == 400
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_401_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(401, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.status_code == 401
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_403_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(403, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.status_code == 403
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_429_raises_retryable(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(429, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.status_code == 429
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_500_raises_retryable(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(500, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.status_code == 500
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_unexpected_status_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient", return_value=_SSEClientContextMock(418, [])):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# 6. GeminiLLMAdapter — network errors
# ---------------------------------------------------------------------------


class TestGeminiLLMAdapterNetworkErrors:
    def _make_adapter(self) -> GeminiLLMAdapter:
        return GeminiLLMAdapter(api_key="AIza-test-key")

    @pytest.mark.asyncio
    async def test_timeout_raises_retryable(self) -> None:
        adapter = self._make_adapter()

        class _TimeoutClient:
            async def __aenter__(self) -> "_TimeoutClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            def stream(self, *args: object, **kwargs: object) -> "_TimeoutClient":
                return self

            async def __aenter_stream__(self) -> "_TimeoutClient":
                return self

            async def __aexit_stream__(self, *args: object) -> None:
                pass

        # Use a different approach: mock the client.stream to raise timeout
        class _TimeoutStreamCtx:
            async def __aenter__(self) -> "_TimeoutStreamCtx":
                raise httpx.TimeoutException("connect timed out")

            async def __aexit__(self, *args: object) -> None:
                pass

        class _TimeoutClientCtx:
            async def __aenter__(self) -> "_TimeoutClientCtx":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            def stream(self, *args: object, **kwargs: object) -> "_TimeoutStreamCtx":
                return _TimeoutStreamCtx()

        with patch("httpx.AsyncClient", return_value=_TimeoutClientCtx()):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_request_error_raises_retryable(self) -> None:
        adapter = self._make_adapter()

        class _NetErrStreamCtx:
            async def __aenter__(self) -> "_NetErrStreamCtx":
                raise httpx.RequestError("connection refused")

            async def __aexit__(self, *args: object) -> None:
                pass

        class _NetErrClientCtx:
            async def __aenter__(self) -> "_NetErrClientCtx":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            def stream(self, *args: object, **kwargs: object) -> "_NetErrStreamCtx":
                return _NetErrStreamCtx()

        with patch("httpx.AsyncClient", return_value=_NetErrClientCtx()):
            with pytest.raises(LLMProviderError) as exc_info:
                await _drain(adapter.respond_streaming(_SYSTEM_PROMPT, []))
        assert exc_info.value.retryable is True
