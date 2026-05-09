"""
ElevenLabsTTSAdapter — locked TTS provider for Waxwing Voice MVP.

Stack decision: ADR-0001 (ElevenLabs Turbo v2.5 replaces VibeVoice, 2026-05-09).

Implementation strategy
-----------------------
The `livekit-plugins-elevenlabs` package provides a first-class LiveKit Agents
TTS plugin. However, it couples tightly to the LiveKit Agents runtime (requires
an active AgentSession context for audio routing). For testability and adapter
isolation, this class wraps the ElevenLabs streaming WebSocket API directly via
`httpx` — the same approach the LiveKit plugin uses internally.

When the LiveKit Agents worker loop is wired (Phase 1 finish-up), VoiceSession
can optionally delegate audio routing to the LiveKit plugin's SynthesizeStream
interface. The TTSAdapter protocol stays stable either way.

Default voice: "Bella" (EXAVITQu4vr4xnSDxMaL)
  — ElevenLabs' recommended phone-quality female voice for Turbo v2.5.
  — Clear, natural, <300ms TTFB on Turbo v2.5.
  — Override with ELEVENLABS_VOICE_ID env var.

Audio format: 16-bit PCM at 16000 Hz (mono) — matches Twilio/LiveKit phone bitrate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx

from voice_agent.providers.tts.protocol import TTSAdapter, TTSProviderError

log = logging.getLogger("voice_agent.providers.tts.elevenlabs")

# ElevenLabs Turbo v2.5 model ID — locked to this per ADR-0001.
_TURBO_V2_5_MODEL = "eleven_turbo_v2_5"

# Streaming endpoint for ElevenLabs text-to-speech.
_STREAM_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

# Audio output format — 16-bit PCM 16kHz mono, compatible with LiveKit/Twilio.
_OUTPUT_FORMAT = "pcm_16000"

# Default chunk size for streaming reads (bytes). ~20ms at 16kHz 16-bit mono.
_CHUNK_SIZE = 640


class ElevenLabsTTSAdapter:
    """
    Streaming TTS adapter backed by ElevenLabs Turbo v2.5.

    Implements the TTSAdapter protocol. Inject this into VoiceSession.__init__
    for production; inject MockTTSAdapter for tests.

    Construction raises ValueError immediately if api_key is None or empty —
    fail fast before any call is attempted.

    Thread-safety: not thread-safe. One instance per VoiceSession (one call).
    cancel() is safe to call from any async context while synthesize_streaming()
    is active (uses asyncio.Event for coordination).
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "EXAVITQu4vr4xnSDxMaL",
        model_id: str = _TURBO_V2_5_MODEL,
        stability: float = 0.5,
        similarity_boost: float = 0.8,
        chunk_size: int = _CHUNK_SIZE,
    ) -> None:
        """
        Args:
            api_key:          ElevenLabs API key. Must be non-empty.
            voice_id:         ElevenLabs voice ID. Defaults to Bella (phone-quality).
            model_id:         Model ID. Locked to eleven_turbo_v2_5 per ADR-0001.
            stability:        Voice stability (0.0–1.0). 0.5 balances clarity and naturalness.
            similarity_boost: Speaker similarity (0.0–1.0). 0.8 keeps voice consistent.
            chunk_size:       Bytes per streaming read. 640 bytes = ~20ms at 16kHz.

        Raises:
            ValueError: If api_key is None or empty.
        """
        if not api_key:
            raise ValueError(
                "ElevenLabsTTSAdapter requires a non-empty api_key. "
                "Set ELEVENLABS_API_KEY in the environment. "
                "See services/voice-agent/.env.example."
            )
        # Never log the api_key — it is a secret.
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._stability = stability
        self._similarity_boost = similarity_boost
        self._chunk_size = chunk_size

        # Cancellation support: set this event to abort an in-progress stream.
        self._cancel_event: asyncio.Event = asyncio.Event()

        log.info(
            "ElevenLabsTTSAdapter initialized",
            extra={"voice_id": voice_id, "model_id": model_id},
        )

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """
        Stream synthesized audio from ElevenLabs.

        Makes a POST to the ElevenLabs streaming TTS endpoint with the Turbo v2.5
        model and the configured voice. Yields PCM chunks as they arrive.

        On barge-in (cancel() called while streaming), stops yielding and returns.
        The stream response is closed cleanly — ElevenLabs does not penalize
        mid-stream disconnects.

        Args:
            text: Utterance text. Voice agent keeps this 1–3 sentences.

        Yields:
            bytes: PCM audio chunks (~20ms per chunk at 16kHz).

        Raises:
            TTSProviderError: On HTTP 4xx/5xx from ElevenLabs, or network failure.
        """
        # Reset cancel event at the start of each synthesis.
        self._cancel_event.clear()

        url = _STREAM_URL_TEMPLATE.format(voice_id=self._voice_id)
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "output_format": _OUTPUT_FORMAT,
            "voice_settings": {
                "stability": self._stability,
                "similarity_boost": self._similarity_boost,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code == 401:
                        raise TTSProviderError(
                            "ElevenLabs authentication failed — check ELEVENLABS_API_KEY.",
                            retryable=False,
                            status_code=401,
                        )
                    if response.status_code == 422:
                        raise TTSProviderError(
                            "ElevenLabs rejected the request — invalid voice_id or parameters.",
                            retryable=False,
                            status_code=422,
                        )
                    if response.status_code == 429:
                        raise TTSProviderError(
                            "ElevenLabs rate limit exceeded.",
                            retryable=True,
                            status_code=429,
                        )
                    if response.status_code >= 500:
                        raise TTSProviderError(
                            f"ElevenLabs server error ({response.status_code}).",
                            retryable=True,
                            status_code=response.status_code,
                        )
                    if response.status_code not in (200, 206):
                        raise TTSProviderError(
                            f"ElevenLabs unexpected status {response.status_code}.",
                            retryable=False,
                            status_code=response.status_code,
                        )

                    async for chunk in response.aiter_bytes(self._chunk_size):
                        if self._cancel_event.is_set():
                            log.debug(
                                "ElevenLabsTTSAdapter: stream cancelled (barge-in)",
                                extra={"voice_id": self._voice_id},
                            )
                            return
                        if chunk:
                            yield chunk

        except httpx.TimeoutException as exc:
            raise TTSProviderError(
                "ElevenLabs request timed out.",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise TTSProviderError(
                f"ElevenLabs network error: {exc}",
                retryable=True,
            ) from exc

    async def cancel(self) -> None:
        """
        Signal the current synthesis stream to stop.

        Sets the cancel event; the streaming loop checks it before each chunk
        and exits cleanly. Idempotent — safe to call when no stream is active.
        """
        self._cancel_event.set()
        log.debug("ElevenLabsTTSAdapter.cancel called", extra={"voice_id": self._voice_id})
