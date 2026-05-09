"""
MockSTTAdapter — deterministic STT adapter for tests.

No network calls. Yields scripted TranscriptionEvent sequences so tests can
assert on turn count, text content, and cancellation behaviour without mocking
httpx internals or providing real audio.

Design
------
The mock takes a list of "scripts" at construction time. Each script is either:
  - A string: yields one TranscriptionEvent(text=s, is_final=True).
  - A list of TranscriptionEvent: yields them in sequence (allows partial events).

Each call to transcribe_streaming() consumes one script entry. If the script list
is exhausted and another call is made, MockSTTAdapter raises AssertionError to
fail loudly rather than silently returning empty text (which could mask test bugs).

Usage in tests::

    # Simple: one turn, one final event
    stt = MockSTTAdapter(scripts=["Hello, I'd like to tour the property."])
    async for event in await stt.transcribe_streaming(audio_iter):
        assert event.text == "Hello, I'd like to tour the property."
        assert event.is_final

    # Multi-turn: scripted conversation
    stt = MockSTTAdapter(scripts=[
        "What's the rent for a one-bedroom?",
        "I have a dog, is that okay?",
        "Can I book a tour for Saturday?",
    ])

    # Cancellation test:
    stt = MockSTTAdapter(scripts=["Hello"])
    gen = stt.transcribe_streaming(audio_iter)
    await stt.cancel()  # cancel before consuming
    events = [e async for e in await gen]
    # No events (cancelled before yield)
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from voice_agent.providers.stt.protocol import STTAdapter, STTProviderError, TranscriptionEvent

log = logging.getLogger("voice_agent.providers.stt.mock")


class MockSTTAdapter:
    """
    Deterministic STT adapter for unit and integration tests.

    Implements the STTAdapter protocol without network calls. Each call to
    transcribe_streaming() yields from the next script entry. Fails loudly
    (AssertionError) if called more times than scripts are provided.

    Tracks call counts so tests can assert how many transcriptions were requested.
    """

    def __init__(
        self,
        scripts: list[str | list[TranscriptionEvent]] | None = None,
        fail_on_next: bool = False,
        fail_retryable: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        """
        Args:
            scripts:        List of scripts to yield. Each entry is either a string
                            (yields one final event) or a list of TranscriptionEvent
                            (yields them in sequence). If None, defaults to an
                            empty list — any call will raise AssertionError.
            fail_on_next:   If True, the next transcribe_streaming call raises
                            STTProviderError instead of yielding events.
            fail_retryable: If fail_on_next is True, whether the error is retryable.
            delay_seconds:  Simulated per-event delay. 0 for fast tests; >0 to test
                            cancel races.
        """
        self._scripts: list[str | list[TranscriptionEvent]] = scripts or []
        self._script_index: int = 0
        self._fail_on_next = fail_on_next
        self._fail_retryable = fail_retryable
        self._delay_seconds = delay_seconds

        self._cancel_event: asyncio.Event = asyncio.Event()
        # Track how many cancels have been "consumed" by transcribe_streaming.
        # Allows pre-cancel (cancel called before transcribe_streaming) to work correctly.
        self._cancel_count_total: int = 0
        self._cancel_count_consumed: int = 0

        # Call tracking — assert on these in tests.
        self.transcribe_call_count: int = 0
        self.cancel_call_count: int = 0
        self.last_audio_chunks_received: int = 0  # count of chunks from last audio_stream

    async def transcribe_streaming(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptionEvent]:
        """
        Yield scripted TranscriptionEvents, ignoring the actual audio content.

        Drains the audio_stream (to correctly simulate a real adapter consuming
        the stream) but does not inspect the bytes. This ensures the caller's
        async generator isn't left open.

        Raises:
            AssertionError: If called more times than scripts are provided.
            STTProviderError: If fail_on_next is True.
        """
        # Check if pre-cancelled (cancel called before this stream started)
        pre_cancelled = self._cancel_count_total > self._cancel_count_consumed
        # Consume one pending cancel if present
        if pre_cancelled:
            self._cancel_count_consumed += 1
        # Reset the async event for this stream's lifetime
        self._cancel_event.clear()
        self.transcribe_call_count += 1

        # Drain the audio stream (simulate consuming input)
        chunk_count = 0
        async for _ in audio_stream:
            chunk_count += 1
            if self._cancel_event.is_set():
                break
        self.last_audio_chunks_received = chunk_count

        # Handle forced failure
        if self._fail_on_next:
            self._fail_on_next = False  # consume the flag
            raise STTProviderError(
                "MockSTTAdapter: simulated transcription failure.",
                retryable=self._fail_retryable,
            )

        # Check if cancelled (either pre-cancel or during audio drain)
        if pre_cancelled or self._cancel_event.is_set():
            log.debug("MockSTTAdapter: cancelled before yielding events")
            return

        # Fail loudly if script is exhausted
        if self._script_index >= len(self._scripts):
            raise AssertionError(
                f"MockSTTAdapter: transcribe_streaming called {self.transcribe_call_count} times "
                f"but only {len(self._scripts)} script(s) were provided. "
                "Add more entries to the scripts list."
            )

        script = self._scripts[self._script_index]
        self._script_index += 1

        # Normalize to list of events
        if isinstance(script, str):
            events = [TranscriptionEvent(text=script, is_final=True, confidence=None)]
        else:
            events = script

        for event in events:
            if self._cancel_event.is_set():
                log.debug("MockSTTAdapter: cancelled mid-script")
                return
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield event

    async def cancel(self) -> None:
        """
        Signal cancellation. Idempotent.
        """
        self.cancel_call_count += 1
        self._cancel_count_total += 1
        self._cancel_event.set()
        log.debug("MockSTTAdapter.cancel called")


# Runtime check — verify MockSTTAdapter satisfies the STTAdapter protocol.
# Runs at import time in dev/test environments and catches protocol drift.
assert isinstance(MockSTTAdapter(), STTAdapter), (
    "MockSTTAdapter does not satisfy STTAdapter protocol. "
    "Check protocol.py and mock.py for method signature drift."
)
