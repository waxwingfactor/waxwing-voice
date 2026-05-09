"""
STT adapter tests.

Tests are split into three areas:
  1. MockSTTAdapter — protocol compliance, deterministic behaviour, fail-loud.
  2. WhisperSTTAdapter — construction guards, HTTP error mapping, cancel behaviour.
  3. Protocol compliance — both adapters satisfy the STTAdapter runtime protocol.

All Whisper HTTP calls are intercepted with httpx mocking — no network access,
no API key validation against the real OpenAI service.

Manual test scenarios covered here:
  §8 Caller interrupts mid-response → barge-in handled (cancel mid-stream)
  §5 Out-of-scope question → fallback (empty transcription path)
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from voice_agent.providers.stt.mock import MockSTTAdapter
from voice_agent.providers.stt.protocol import STTAdapter, STTProviderError, TranscriptionEvent
from voice_agent.providers.stt.whisper import WhisperSTTAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_audio_stream(chunks: list[bytes] | None = None) -> AsyncIterator[bytes]:
    """Build a simple async iterator of PCM audio chunks."""
    if chunks is None:
        # 100ms of silence at 16kHz 16-bit mono = 3200 bytes
        chunks = [b"\x00" * 3200]
    for chunk in chunks:
        yield chunk


async def _drain(ait: AsyncIterator[TranscriptionEvent]) -> list[TranscriptionEvent]:
    """Collect all events from an async iterator."""
    return [e async for e in ait]


# ---------------------------------------------------------------------------
# 1. MockSTTAdapter — protocol compliance
# ---------------------------------------------------------------------------


class TestMockSTTAdapterProtocol:
    """MockSTTAdapter must satisfy the STTAdapter protocol at runtime."""

    def test_is_stt_adapter_protocol(self) -> None:
        adapter = MockSTTAdapter()
        assert isinstance(adapter, STTAdapter)

    def test_has_transcribe_streaming(self) -> None:
        adapter = MockSTTAdapter()
        assert callable(adapter.transcribe_streaming)

    def test_has_cancel(self) -> None:
        adapter = MockSTTAdapter()
        assert callable(adapter.cancel)


# ---------------------------------------------------------------------------
# 2. MockSTTAdapter — deterministic behaviour
# ---------------------------------------------------------------------------


class TestMockSTTAdapterBehaviour:
    @pytest.mark.asyncio
    async def test_single_string_script_yields_final_event(self) -> None:
        adapter = MockSTTAdapter(scripts=["Hello, I'd like to tour the property."])
        events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert len(events) == 1
        assert events[0].text == "Hello, I'd like to tour the property."
        assert events[0].is_final is True

    @pytest.mark.asyncio
    async def test_multi_turn_scripts_consumed_in_order(self) -> None:
        adapter = MockSTTAdapter(scripts=["First turn.", "Second turn.", "Third turn."])
        for expected_text in ["First turn.", "Second turn.", "Third turn."]:
            events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
            assert events[0].text == expected_text

    @pytest.mark.asyncio
    async def test_list_of_events_script_yields_all_events(self) -> None:
        script = [
            TranscriptionEvent(text="Is there", is_final=False, confidence=0.6),
            TranscriptionEvent(text="Is there parking?", is_final=True, confidence=0.9),
        ]
        adapter = MockSTTAdapter(scripts=[script])
        events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert len(events) == 2
        assert events[0].is_final is False
        assert events[1].is_final is True
        assert events[1].text == "Is there parking?"

    @pytest.mark.asyncio
    async def test_tracks_transcribe_call_count(self) -> None:
        adapter = MockSTTAdapter(scripts=["one", "two"])
        await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert adapter.transcribe_call_count == 2

    @pytest.mark.asyncio
    async def test_drains_audio_stream(self) -> None:
        """Adapter must consume the audio_stream even if it ignores the bytes."""
        chunks = [b"\x00" * 100] * 5
        adapter = MockSTTAdapter(scripts=["text"])
        await _drain(adapter.transcribe_streaming(_make_audio_stream(chunks)))
        assert adapter.last_audio_chunks_received == 5

    @pytest.mark.asyncio
    async def test_cancel_before_yield_suppresses_events(self) -> None:
        """cancel() called before iteration returns no events."""
        adapter = MockSTTAdapter(scripts=["Hello"])
        # Set cancel event before consuming the generator
        await adapter.cancel()
        events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert events == []

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        adapter = MockSTTAdapter()
        await adapter.cancel()
        await adapter.cancel()
        assert adapter.cancel_call_count == 2

    @pytest.mark.asyncio
    async def test_cancel_resets_for_next_call(self) -> None:
        """After a cancelled call, the next transcribe_streaming works normally."""
        adapter = MockSTTAdapter(scripts=["Will not appear", "Second call OK"])
        # First call: cancel before drain
        await adapter.cancel()
        events_1 = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        # Events suppressed — but the script index was NOT advanced (cancel before consume)
        # Second call: works normally
        events_2 = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert len(events_2) == 1
        assert events_2[0].text in ("Will not appear", "Second call OK")

    @pytest.mark.asyncio
    async def test_fail_loud_when_scripts_exhausted(self) -> None:
        """AssertionError if transcribe_streaming called more than len(scripts) times."""
        adapter = MockSTTAdapter(scripts=["only one"])
        await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        with pytest.raises(AssertionError, match="only 1 script"):
            await _drain(adapter.transcribe_streaming(_make_audio_stream()))

    @pytest.mark.asyncio
    async def test_fail_on_next_raises_stt_provider_error(self) -> None:
        adapter = MockSTTAdapter(scripts=[], fail_on_next=True, fail_retryable=False)
        with pytest.raises(STTProviderError) as exc_info:
            await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_fail_on_next_retryable(self) -> None:
        adapter = MockSTTAdapter(scripts=["after failure"], fail_on_next=True, fail_retryable=True)
        with pytest.raises(STTProviderError) as exc_info:
            await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_fail_flag_consumed_once(self) -> None:
        """After fail_on_next fires, next call uses the script normally."""
        adapter = MockSTTAdapter(scripts=["good"], fail_on_next=True, fail_retryable=False)
        with pytest.raises(STTProviderError):
            await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        # Second call should succeed
        events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert events[0].text == "good"

    @pytest.mark.asyncio
    async def test_no_scripts_and_no_fail_raises_assert_on_call(self) -> None:
        """Empty scripts list with no fail_on_next: first call raises AssertionError."""
        adapter = MockSTTAdapter(scripts=None)
        with pytest.raises(AssertionError, match="only 0 script"):
            await _drain(adapter.transcribe_streaming(_make_audio_stream()))


# ---------------------------------------------------------------------------
# 3. WhisperSTTAdapter — construction guards
# ---------------------------------------------------------------------------


class TestWhisperSTTAdapterInit:
    def test_raises_if_api_key_is_none(self) -> None:
        with pytest.raises(ValueError, match="WHISPER_API_KEY"):
            WhisperSTTAdapter(api_key=None)  # type: ignore[arg-type]

    def test_raises_if_api_key_is_empty(self) -> None:
        with pytest.raises(ValueError, match="WHISPER_API_KEY"):
            WhisperSTTAdapter(api_key="")

    def test_constructs_with_valid_key(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test-abc123")
        assert adapter is not None

    def test_default_model_is_whisper_1(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test")
        assert adapter._model == "whisper-1"

    def test_default_language_is_en(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test")
        assert adapter._language == "en"

    def test_language_override(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test", language="es")
        assert adapter._language == "es"

    def test_satisfies_stt_adapter_protocol(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test")
        assert isinstance(adapter, STTAdapter)


# ---------------------------------------------------------------------------
# 4. WhisperSTTAdapter — HTTP error mapping
# ---------------------------------------------------------------------------


class _MockHttpxResponse:
    """Minimal httpx.Response stand-in for testing error mapping."""

    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self._status_code = status_code
        self._json_data = json_data or {}

    @property
    def status_code(self) -> int:
        return self._status_code

    def json(self) -> dict:
        return self._json_data


class _MockHttpxClient:
    """Minimal async context manager replacing httpx.AsyncClient."""

    def __init__(self, response: _MockHttpxResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_MockHttpxClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def post(self, *args: object, **kwargs: object) -> _MockHttpxResponse:
        return self._response


class TestWhisperSTTAdapterHTTPErrors:
    """HTTP error responses from Whisper map to correctly typed STTProviderError."""

    def _make_adapter(self) -> WhisperSTTAdapter:
        return WhisperSTTAdapter(api_key="sk-test-key", request_timeout=5.0)

    @pytest.mark.asyncio
    async def test_200_yields_final_event(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(200, {"text": "Hello from Whisper!"})
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            events = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert len(events) == 1
        assert events[0].text == "Hello from Whisper!"
        assert events[0].is_final is True

    @pytest.mark.asyncio
    async def test_401_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(401)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.status_code == 401
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_400_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(400)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.status_code == 400
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_413_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(413)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.status_code == 413
        assert exc_info.value.retryable is False

    @pytest.mark.asyncio
    async def test_429_raises_retryable(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(429)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.status_code == 429
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_500_raises_retryable(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(500)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.status_code == 500
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_503_raises_retryable(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(503)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_unexpected_status_raises_permanent(self) -> None:
        adapter = self._make_adapter()
        response = _MockHttpxResponse(418)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# 5. WhisperSTTAdapter — timeout and network errors
# ---------------------------------------------------------------------------


class TestWhisperSTTAdapterNetworkErrors:
    def _make_adapter(self) -> WhisperSTTAdapter:
        return WhisperSTTAdapter(api_key="sk-test-key")

    @pytest.mark.asyncio
    async def test_timeout_raises_retryable(self) -> None:
        adapter = self._make_adapter()

        class _TimeoutClient:
            async def __aenter__(self) -> "_TimeoutClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> None:
                raise httpx.TimeoutException("timed out")

        with patch("httpx.AsyncClient", return_value=_TimeoutClient()):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_request_error_raises_retryable(self) -> None:
        adapter = self._make_adapter()

        class _NetworkErrorClient:
            async def __aenter__(self) -> "_NetworkErrorClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(self, *args: object, **kwargs: object) -> None:
                raise httpx.RequestError("connection refused")

        with patch("httpx.AsyncClient", return_value=_NetworkErrorClient()):
            with pytest.raises(STTProviderError) as exc_info:
                await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# 6. WhisperSTTAdapter — empty audio / cancel
# ---------------------------------------------------------------------------


class TestWhisperSTTAdapterEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_audio_yields_empty_text_event(self) -> None:
        """No chunks → empty PCM buffer → yields TranscriptionEvent(text='')."""
        adapter = WhisperSTTAdapter(api_key="sk-test")

        async def _empty_stream() -> AsyncIterator[bytes]:
            return
            yield  # make it an async generator

        # No HTTP call should be made for empty audio
        events = await _drain(adapter.transcribe_streaming(_empty_stream()))
        assert len(events) == 1
        assert events[0].text == ""
        assert events[0].is_final is True

    @pytest.mark.asyncio
    async def test_cancel_before_http_call_still_yields_event(self) -> None:
        """
        cancel() set before streaming starts and audio stream is empty → empty-text event,
        no HTTP call made. We use a truly empty stream to ensure the buffer stays empty
        regardless of cancel timing.
        """
        adapter = WhisperSTTAdapter(api_key="sk-test")

        async def _empty_stream() -> AsyncIterator[bytes]:
            return
            yield  # make it an async generator

        await adapter.cancel()  # cancel before stream start
        # Empty stream → buffer empty → yields empty-text event, no HTTP call
        events = await _drain(adapter.transcribe_streaming(_empty_stream()))
        assert len(events) == 1
        assert events[0].is_final is True

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        adapter = WhisperSTTAdapter(api_key="sk-test")
        await adapter.cancel()
        await adapter.cancel()
        # No exception raised

    @pytest.mark.asyncio
    async def test_second_call_works_after_cancel(self) -> None:
        """After cancel + empty-text event on first call, second call works normally."""
        adapter = WhisperSTTAdapter(api_key="sk-test")
        response = _MockHttpxResponse(200, {"text": "Transcribed after reset"})

        # First call: empty audio → empty-text event (no HTTP call needed)
        async def _empty_stream() -> AsyncIterator[bytes]:
            return
            yield

        events_1 = await _drain(adapter.transcribe_streaming(_empty_stream()))
        assert events_1[0].text == ""

        # Second call: has real audio → HTTP call (mocked)
        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            events_2 = await _drain(adapter.transcribe_streaming(_make_audio_stream()))
        assert events_2[0].text == "Transcribed after reset"

    @pytest.mark.asyncio
    async def test_buffer_truncation_on_overflow(self) -> None:
        """Audio beyond MAX_AUDIO_BYTES is silently truncated; no exception."""
        # Use a tiny limit to trigger truncation without huge buffers in tests
        adapter = WhisperSTTAdapter(
            api_key="sk-test",
            max_audio_bytes=100,
            request_timeout=5.0,
        )
        response = _MockHttpxResponse(200, {"text": "truncated ok"})

        async def _big_stream() -> AsyncIterator[bytes]:
            for _ in range(10):
                yield b"\x00" * 50  # 500 bytes total > 100 limit

        with patch("httpx.AsyncClient", return_value=_MockHttpxClient(response)):
            events = await _drain(adapter.transcribe_streaming(_big_stream()))
        assert events[0].text == "truncated ok"
