"""
TTSAdapter protocol — the stable interface all TTS adapters must implement.

ADR-0001 (ElevenLabs replaces VibeVoice) locks ElevenLabs Turbo v2.5 as the
production adapter. The protocol exists so that:
  1. Tests inject MockTTSAdapter without network calls.
  2. A future provider swap (ADR required) only touches the adapter class,
     not VoiceSession or any conversation logic.

Usage::

    adapter: TTSAdapter = ElevenLabsTTSAdapter(api_key=..., voice_id=...)
    async for chunk in adapter.synthesize_streaming("Hello!"):
        # push chunk to LiveKit audio sink
        ...
    # On barge-in:
    await adapter.cancel()
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class TTSAdapter(Protocol):
    """
    Protocol for streaming TTS adapters.

    Adapters must be safe for concurrent use within a single call session
    (one synthesize_streaming call active at a time). The cancel() method
    must be idempotent — calling it when no stream is active is a no-op.
    """

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """
        Stream synthesized audio as raw PCM or codec-encoded bytes.

        Yields audio chunks as they arrive from the TTS provider.
        Chunks should be small enough for real-time playback (~20–40ms
        of audio per chunk at 8kHz/16kHz phone bitrate).

        Args:
            text: The utterance text to synthesize. Voice agent keeps
                  these short (1–3 sentences) for phone-length responses.

        Yields:
            bytes: Raw audio chunk. Encoding is adapter-specific; callers
                   must be aware of the format (e.g., 16-bit PCM at 16kHz).

        Raises:
            TTSProviderError: On unrecoverable synthesis failure.
        """
        ...  # pragma: no cover

    async def cancel(self) -> None:
        """
        Abort the current synthesis stream (barge-in support).

        Must be safe to call even if no synthesis is in progress.
        After cancel(), a new synthesize_streaming() call must work
        without needing to recreate the adapter.
        """
        ...  # pragma: no cover


class TTSProviderError(Exception):
    """
    Raised by TTS adapters on unrecoverable synthesis failures.

    Attributes:
        message:   Human-readable description.
        retryable: True if the caller may retry after a short wait.
        status_code: HTTP status code from the provider, if available.
    """

    def __init__(
        self,
        message: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code

    def __repr__(self) -> str:
        return (
            f"TTSProviderError(message={self.message!r}, "
            f"retryable={self.retryable}, status_code={self.status_code})"
        )
