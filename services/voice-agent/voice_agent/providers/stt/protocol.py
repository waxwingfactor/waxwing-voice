"""
STTAdapter protocol — the stable interface all STT adapters must implement.

The locked STT provider is hosted OpenAI Whisper (WHISPER_API_KEY). The protocol
exists so that:
  1. Tests inject MockSTTAdapter without network calls.
  2. A future provider swap (ADR required) only touches the adapter class, not the
     LiveKit worker entrypoint or VoiceSession.

Design notes
------------
- Audio input is an AsyncIterator[bytes] of 16-bit PCM at 16kHz (mono). This is
  the format delivered by the LiveKit AudioStream after the Twilio bridge resamples
  from 8kHz mu-law (see ADR-0004).
- Transcriptions are yielded as TranscriptionEvent objects. The worker consumes
  is_final=True events for LLM turns; is_final=False events for barge-in detection.
- cancel() must abort an in-progress transcription stream. The caller (worker) uses
  this when the call ends or a new utterance begins before the previous one completes.

Usage::

    adapter: STTAdapter = WhisperSTTAdapter(api_key=...)
    async for event in adapter.transcribe_streaming(audio_stream):
        if event.is_final:
            response = await llm.respond(event.text)
        elif event.confidence and event.confidence > 0.7:
            await tts.cancel()  # barge-in: caller is speaking confidently
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class TranscriptionEvent:
    """
    A single transcription result from an STT provider.

    Attributes:
        text:       The transcribed text. May be partial (is_final=False) or
                    complete (is_final=True) for the current utterance.
        is_final:   True when the provider considers the utterance complete.
                    Partial events (is_final=False) are useful for barge-in
                    detection — if the caller is speaking confidently, cancel TTS.
        confidence: Provider confidence score in [0.0, 1.0], or None if the
                    provider does not return per-segment confidence. Whisper's
                    REST API does not return per-word confidence; this is None
                    for WhisperSTTAdapter's final events. Real-time adapters
                    (e.g., Deepgram, Google STT) do return confidence — this
                    field is reserved for future adapters or for providers that
                    do return it.
    """

    text: str
    is_final: bool
    confidence: float | None = None


@runtime_checkable
class STTAdapter(Protocol):
    """
    Protocol for streaming STT adapters.

    Adapters must be safe for sequential use within a single call session
    (one transcribe_streaming call active at a time). The cancel() method
    must be idempotent — calling it when no stream is active is a no-op.

    Audio format contract (callers must respect this):
      - 16-bit signed PCM, little-endian
      - 16000 Hz sample rate (16kHz)
      - Mono (1 channel)
      - Chunks of any size — adapter buffers internally as needed
    """

    async def transcribe_streaming(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptionEvent]:
        """
        Transcribe a streaming audio input into text events.

        Consumes the audio_stream and yields TranscriptionEvent objects.
        For providers that only support batch transcription (e.g., OpenAI
        Whisper REST), the adapter accumulates audio into a buffer and yields
        a single is_final=True event at end of stream. For streaming providers,
        partial events are yielded with is_final=False followed by a final event.

        Args:
            audio_stream: AsyncIterator of 16-bit PCM audio chunks at 16kHz mono.

        Yields:
            TranscriptionEvent: One or more transcription events. The last event
                                for each utterance always has is_final=True.

        Raises:
            STTProviderError: On unrecoverable transcription failure.
        """
        ...  # pragma: no cover

    async def cancel(self) -> None:
        """
        Abort the current transcription stream.

        Must be safe to call even if no transcription is in progress.
        After cancel(), a new transcribe_streaming() call must work
        without needing to recreate the adapter.
        """
        ...  # pragma: no cover


class STTProviderError(Exception):
    """
    Raised by STT adapters on unrecoverable transcription failures.

    Attributes:
        message:     Human-readable description.
        retryable:   True if the caller may retry after a short wait.
                     4xx errors (except 429) are not retryable.
                     5xx and 429 are retryable.
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
            f"STTProviderError(message={self.message!r}, "
            f"retryable={self.retryable}, status_code={self.status_code})"
        )
