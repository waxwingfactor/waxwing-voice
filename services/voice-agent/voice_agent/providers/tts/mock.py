"""
MockTTSAdapter — deterministic TTS adapter for tests.

No network calls. Yields predictable bytes so tests can assert on chunk
count, size, and cancellation behaviour without mocking httpx internals.

Usage in tests::

    adapter = MockTTSAdapter()
    chunks = [chunk async for chunk in await adapter.synthesize_streaming("Hello!")]
    assert len(chunks) == MockTTSAdapter.CHUNKS_PER_UTTERANCE

    # Barge-in test:
    gen = await adapter.synthesize_streaming("Hello!")
    await adapter.cancel()
    chunks = [chunk async for chunk in gen]
    assert len(chunks) == 0  # cancelled before first chunk
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from voice_agent.providers.tts.protocol import TTSAdapter, TTSProviderError

log = logging.getLogger("voice_agent.providers.tts.mock")

# Each synthesize_streaming call yields this many chunks by default.
# Chosen to be small enough for fast tests but large enough to exercise
# the streaming loop (more than 1 chunk).
_DEFAULT_CHUNKS = 4

# Each chunk is this many bytes. 640 bytes = 20ms at 16kHz 16-bit mono,
# matching the ElevenLabs adapter's default chunk size.
_CHUNK_SIZE = 640

# Fill byte for deterministic output — 0x42 so tests can assert on content.
_FILL_BYTE = 0x42


class MockTTSAdapter:
    """
    Deterministic TTS adapter for unit and integration tests.

    Implements the TTSAdapter protocol without network calls. Behaviour is
    fully predictable: yields exactly `chunks_per_utterance` fixed-size chunks
    unless cancel() is called, in which case the stream stops immediately.

    Tracks call counts so tests can assert how many times synthesis was requested.
    """

    #: Default number of chunks yielded per synthesize_streaming call.
    CHUNKS_PER_UTTERANCE: int = _DEFAULT_CHUNKS

    #: Byte size of each chunk — matches ElevenLabsTTSAdapter default.
    CHUNK_SIZE: int = _CHUNK_SIZE

    def __init__(
        self,
        chunks_per_utterance: int = _DEFAULT_CHUNKS,
        fail_on_next: bool = False,
        fail_retryable: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        """
        Args:
            chunks_per_utterance: How many chunks to yield per call.
            fail_on_next:         If True, the next synthesize_streaming call
                                  raises TTSProviderError instead of yielding.
            fail_retryable:       If fail_on_next is True, whether the error
                                  should have retryable=True.
            delay_seconds:        Simulated per-chunk delay. Keep at 0 for fast tests;
                                  set >0 to test timeout/cancel races.
        """
        self._chunks_per_utterance = chunks_per_utterance
        self._fail_on_next = fail_on_next
        self._fail_retryable = fail_retryable
        self._delay_seconds = delay_seconds

        self._cancel_event: asyncio.Event = asyncio.Event()

        # Call tracking — assert on these in tests.
        self.synthesize_call_count: int = 0
        self.cancel_call_count: int = 0
        self.last_synthesized_text: str | None = None

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """
        Yield deterministic PCM chunks.

        Checks cancel event before each chunk. If fail_on_next is set,
        raises TTSProviderError instead of yielding any chunks.
        """
        self._cancel_event.clear()
        self.synthesize_call_count += 1
        self.last_synthesized_text = text

        if self._fail_on_next:
            self._fail_on_next = False  # consume the flag
            raise TTSProviderError(
                "MockTTSAdapter: simulated synthesis failure.",
                retryable=self._fail_retryable,
            )

        for _ in range(self._chunks_per_utterance):
            if self._cancel_event.is_set():
                log.debug("MockTTSAdapter: stream cancelled")
                return
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield bytes([_FILL_BYTE] * self.CHUNK_SIZE)

    async def cancel(self) -> None:
        """
        Signal cancellation. Idempotent.
        """
        self.cancel_call_count += 1
        self._cancel_event.set()
        log.debug("MockTTSAdapter.cancel called")


# Runtime check — verify MockTTSAdapter satisfies the TTSAdapter protocol.
# This runs at import time in dev/test environments and catches protocol drift.
assert isinstance(MockTTSAdapter(), TTSAdapter), (
    "MockTTSAdapter does not satisfy TTSAdapter protocol. "
    "Check protocol.py and mock.py for method signature drift."
)
