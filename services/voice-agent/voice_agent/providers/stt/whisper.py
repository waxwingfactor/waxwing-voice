"""
WhisperSTTAdapter — locked STT provider for Waxwing Voice MVP.

Stack decision: hosted OpenAI Whisper API via httpx (direct REST, no openai SDK).
Keeping deps lean — the openai Python SDK adds significant transitive dependencies
that we don't need since we only call one endpoint.

Implementation strategy
-----------------------
OpenAI's Whisper API is a batch endpoint (POST /v1/audio/transcriptions). It does
not support true streaming transcription. Our adapter therefore:

  1. Accumulates all audio chunks from the LiveKit AudioStream into an in-memory
     buffer (capped at MAX_AUDIO_BYTES to prevent unbounded memory growth).
  2. On stream exhaustion (or cancel()), POST the buffer to OpenAI Whisper as a
     multipart/form-data upload with model=whisper-1 and response_format=json.
  3. Yields a single TranscriptionEvent(is_final=True) with the returned text.

This means the STT adapter does NOT emit partial (is_final=False) events — only
a final event when the utterance buffer is complete. Barge-in detection therefore
relies on the LiveKit Agents VAD (Voice Activity Detection) layer, not on partial
STT events.

Audio format: 16-bit signed PCM, little-endian, 16kHz mono.
The buffer is written as a .wav file (with a minimal WAV header) before upload.

Buffer limits
-------------
MAX_AUDIO_SECONDS = 30  (phone utterances rarely exceed 30 seconds)
At 16kHz, 16-bit mono: 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec.
MAX_AUDIO_BYTES = 30 * 32000 = 960_000 bytes (~960KB). Well within memory bounds.

Error mapping
-------------
- 401 Unauthorized       → STTProviderError(retryable=False)  — bad API key
- 400 Bad Request        → STTProviderError(retryable=False)  — invalid audio
- 413 Payload Too Large  → STTProviderError(retryable=False)  — audio too long
- 429 Rate Limited       → STTProviderError(retryable=True)   — back off
- 5xx Server Error       → STTProviderError(retryable=True)   — transient
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import time
import wave
from typing import AsyncIterator

import httpx

from voice_agent.providers.stt.protocol import STTAdapter, STTProviderError, TranscriptionEvent

log = logging.getLogger("voice_agent.providers.stt.whisper")

# OpenAI Whisper transcription endpoint.
_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"

# Locked to whisper-1 — the only hosted Whisper model OpenAI exposes via REST.
_WHISPER_MODEL = "whisper-1"

# Audio parameters — must match LiveKit AudioStream output (see ADR-0004).
_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # bytes (16-bit)

# Maximum audio buffer size. 30 seconds at 16kHz 16-bit mono.
_MAX_AUDIO_SECONDS = 30
_MAX_AUDIO_BYTES = _MAX_AUDIO_SECONDS * _SAMPLE_RATE * _CHANNELS * _SAMPLE_WIDTH  # 960_000

# HTTP timeout for the Whisper API call. Whisper processes ~10s of audio in ~2s.
# 30-second utterance might take up to ~8s. We allow 20s before timing out.
_REQUEST_TIMEOUT = 20.0


def _build_wav_bytes(pcm_bytes: bytes) -> bytes:
    """
    Wrap raw 16-bit PCM bytes in a minimal WAV container.

    OpenAI Whisper accepts WAV files. This builds a well-formed WAV header
    in-memory without writing to disk.

    Args:
        pcm_bytes: Raw 16-bit signed little-endian PCM at 16kHz mono.

    Returns:
        bytes: Complete WAV file content ready for upload.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(_CHANNELS)
        wav_file.setsampwidth(_SAMPLE_WIDTH)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)
    return buf.getvalue()


class WhisperSTTAdapter:
    """
    Batch STT adapter backed by OpenAI's hosted Whisper API.

    Implements the STTAdapter protocol. Audio is accumulated per-utterance
    and submitted as a single WAV file to the Whisper transcription endpoint.
    Yields one TranscriptionEvent(is_final=True) per call.

    Construction raises ValueError immediately if api_key is None or empty —
    fail fast before any call is attempted.

    Thread-safety: not thread-safe. One instance per VoiceSession (one call).
    cancel() is safe to call from any async context while transcribe_streaming()
    is active.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _WHISPER_MODEL,
        language: str | None = "en",
        max_audio_bytes: int = _MAX_AUDIO_BYTES,
        request_timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        """
        Args:
            api_key:         OpenAI API key for Whisper. Must be non-empty.
            model:           Whisper model. Locked to 'whisper-1' for MVP.
            language:        ISO-639-1 language hint. Defaults to 'en'. None = auto-detect.
            max_audio_bytes: Maximum bytes to buffer. Chunks beyond this are silently dropped.
            request_timeout: HTTP timeout in seconds for the transcription API call.

        Raises:
            ValueError: If api_key is None or empty.
        """
        if not api_key:
            raise ValueError(
                "WhisperSTTAdapter requires a non-empty api_key. "
                "Set WHISPER_API_KEY in the environment. "
                "See services/voice-agent/.env.example."
            )
        # Never log the api_key — it is a secret.
        self._api_key = api_key
        self._model = model
        self._language = language
        self._max_audio_bytes = max_audio_bytes
        self._request_timeout = request_timeout

        # Cancellation support: set this event to abort an in-progress stream.
        self._cancel_event: asyncio.Event = asyncio.Event()

        log.info(
            "WhisperSTTAdapter initialized",
            extra={"model": model, "language": language},
        )

    async def transcribe_streaming(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptionEvent]:
        """
        Transcribe a streaming audio input via OpenAI Whisper.

        Accumulates all chunks from audio_stream into a PCM buffer, then POSTs
        to the Whisper API as a WAV file. Yields a single final TranscriptionEvent.

        If the audio buffer is empty (caller was silent), yields an event with
        text="" and is_final=True so the caller can handle silence gracefully.

        If cancel() is called before the stream is exhausted, collection stops
        and any already-buffered audio is submitted. This allows partial utterances
        to be transcribed rather than discarded entirely.

        Args:
            audio_stream: AsyncIterator of 16-bit PCM audio chunks at 16kHz mono.

        Yields:
            TranscriptionEvent: Single final event with the transcribed text.

        Raises:
            STTProviderError: On HTTP 4xx/5xx from the Whisper API, or network failure.
        """
        self._cancel_event.clear()

        # --- Phase 1: Accumulate audio buffer ---
        pcm_buffer = bytearray()
        try:
            async for chunk in audio_stream:
                if self._cancel_event.is_set():
                    log.debug("WhisperSTTAdapter: stream cancelled during audio collection")
                    break
                if len(pcm_buffer) + len(chunk) <= self._max_audio_bytes:
                    pcm_buffer.extend(chunk)
                else:
                    # Buffer full — truncate and stop collecting
                    remaining = self._max_audio_bytes - len(pcm_buffer)
                    if remaining > 0:
                        pcm_buffer.extend(chunk[:remaining])
                    log.warning(
                        "WhisperSTTAdapter: audio buffer limit reached, truncating",
                        extra={"max_bytes": self._max_audio_bytes},
                    )
                    break
        except Exception as exc:
            log.error(
                "WhisperSTTAdapter: error reading audio stream",
                extra={"error": str(exc)[:200]},
            )
            raise STTProviderError(
                f"Error reading audio stream: {exc}",
                retryable=False,
            ) from exc

        # Handle empty buffer (silence)
        if not pcm_buffer:
            log.debug("WhisperSTTAdapter: empty audio buffer, returning empty transcription")
            yield TranscriptionEvent(text="", is_final=True, confidence=None)
            return

        # --- Phase 2: Build WAV and submit to Whisper API ---
        wav_bytes = _build_wav_bytes(bytes(pcm_buffer))

        audio_duration = len(pcm_buffer) / (_SAMPLE_RATE * _SAMPLE_WIDTH * _CHANNELS)
        log.info(
            "WhisperSTTAdapter: submitting to API — pcm_bytes=%d wav_bytes=%d audio_secs=%.2f",
            len(pcm_buffer), len(wav_bytes), audio_duration,
        )

        t0 = time.monotonic()
        text = await self._call_whisper_api(wav_bytes)
        elapsed = time.monotonic() - t0

        log.info(
            "WhisperSTTAdapter: API returned in %.2fs — text=%.120r",
            elapsed, text,
        )

        yield TranscriptionEvent(text=text, is_final=True, confidence=None)

    async def cancel(self) -> None:
        """
        Signal the current transcription to stop collecting audio.

        Sets the cancel event. If audio collection is in progress, it stops
        after the current chunk and any already-buffered audio is submitted.
        Idempotent — safe to call when no transcription is active.
        """
        self._cancel_event.set()
        log.debug("WhisperSTTAdapter.cancel called")

    async def _call_whisper_api(self, wav_bytes: bytes) -> str:
        """
        POST WAV audio to the OpenAI Whisper API and return the transcript text.

        Args:
            wav_bytes: Complete WAV file content.

        Returns:
            Transcribed text string. Empty string if the response text is blank.

        Raises:
            STTProviderError: On HTTP error or network failure.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        # Multipart form: model + optional language + audio file
        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav"),
            "model": (None, self._model),
        }
        if self._language:
            files["language"] = (None, self._language)

        try:
            async with httpx.AsyncClient(timeout=self._request_timeout) as client:
                response = await client.post(
                    _WHISPER_URL,
                    headers=headers,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise STTProviderError(
                "Whisper API request timed out.",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise STTProviderError(
                f"Whisper API network error: {exc}",
                retryable=True,
            ) from exc

        # Map HTTP status codes to STTProviderError
        if response.status_code == 200:
            try:
                data = response.json()
                return str(data.get("text", "")).strip()
            except Exception as exc:
                raise STTProviderError(
                    "Whisper API returned invalid JSON.",
                    retryable=False,
                ) from exc

        if response.status_code == 401:
            raise STTProviderError(
                "Whisper API authentication failed — check WHISPER_API_KEY.",
                retryable=False,
                status_code=401,
            )
        if response.status_code == 400:
            raise STTProviderError(
                "Whisper API rejected the audio — check format (16kHz PCM WAV).",
                retryable=False,
                status_code=400,
            )
        if response.status_code == 413:
            raise STTProviderError(
                "Whisper API: audio payload too large.",
                retryable=False,
                status_code=413,
            )
        if response.status_code == 429:
            raise STTProviderError(
                "Whisper API rate limit exceeded.",
                retryable=True,
                status_code=429,
            )
        if response.status_code >= 500:
            raise STTProviderError(
                f"Whisper API server error ({response.status_code}).",
                retryable=True,
                status_code=response.status_code,
            )
        raise STTProviderError(
            f"Whisper API unexpected status {response.status_code}.",
            retryable=False,
            status_code=response.status_code,
        )
