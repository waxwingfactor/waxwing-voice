"""
Audio I/O bridge between LiveKit and the STT/TTS adapters.

Architecture (ADR-0004):
  - Harsha's backend resamples Twilio mu-law (8kHz) → 16kHz PCM before publishing
    to the LiveKit room. The voice-agent worker therefore receives 16kHz PCM from
    the LiveKit AudioStream — no resampling needed on the voice-agent side.
  - TTS output (ElevenLabs 16kHz PCM) goes directly to the LiveKit AudioSource.

This module provides:
  - AudioStreamAdapter: wraps a LiveKit AudioStream as an AsyncIterator[bytes]
    that the STT adapter can consume.
  - AudioSinkAdapter: wraps a LiveKit AudioSource so TTS chunks can be pushed in.

LiveKit audio frame format (livekit-agents >=0.10):
  - AudioFrame: contains samples_per_channel (int), sample_rate (int),
    num_channels (int), data (bytes of 16-bit signed little-endian PCM)
  - AudioStream is an async iterable of AudioFrameEvent objects.
  - AudioSource.capture_frame(frame) pushes audio to the room.

Import guard: livekit.agents is not installed in the test environment. All
LiveKit types are imported inside try/except so that tests can import this module
without the package installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncIterator

log = logging.getLogger("voice_agent.worker.audio_io")

# Guard against missing livekit-agents during test runs.
try:
    from livekit import rtc
    from livekit.agents import JobContext
    _LIVEKIT_AVAILABLE = True
except ImportError:
    rtc = None  # type: ignore[assignment]
    JobContext = None  # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False

# Audio format constants — must match the LiveKit room's audio track format.
# Harsha's bridge publishes at 16kHz mono 16-bit PCM (see ADR-0004).
SAMPLE_RATE = 16_000
NUM_CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes (16-bit)
# 20ms frame size — LiveKit's standard audio frame duration.
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = (SAMPLE_RATE * FRAME_DURATION_MS) // 1000  # 320 samples
BYTES_PER_FRAME = SAMPLES_PER_FRAME * NUM_CHANNELS * SAMPLE_WIDTH  # 640 bytes


class AudioStreamAdapter:
    """
    Wraps a LiveKit AudioStream as an AsyncIterator[bytes] of 16-bit PCM chunks.

    The STT adapter (WhisperSTTAdapter or MockSTTAdapter) consumes an
    AsyncIterator[bytes]. This adapter converts LiveKit AudioFrameEvent objects
    into raw PCM bytes at the same rate they arrive from the room.

    Usage::

        stream_adapter = AudioStreamAdapter(livekit_audio_stream)
        async for event in stt.transcribe_streaming(stream_adapter.as_async_iter()):
            ...
    """

    def __init__(self, audio_stream: object) -> None:
        """
        Args:
            audio_stream: LiveKit AudioStream (async iterable of AudioFrameEvent).
                          Type is object because livekit may not be importable.
        """
        self._stream = audio_stream
        self._cancelled = False

    def cancel(self) -> None:
        """Signal this adapter to stop yielding chunks."""
        self._cancelled = True

    async def as_async_iter(self) -> AsyncIterator[bytes]:
        """
        Yield raw 16-bit PCM bytes from the LiveKit AudioStream.

        Each LiveKit AudioFrameEvent has a frame.data attribute (bytes of PCM).
        We yield that directly — Harsha's bridge ensures it is already 16kHz mono.

        Stops when cancelled or when the LiveKit stream ends.
        """
        if not _LIVEKIT_AVAILABLE:
            # Test/offline mode: yield nothing. Tests inject a real async iterator
            # directly into the STT adapter rather than using this class.
            log.debug("AudioStreamAdapter: livekit not available, yielding nothing")
            return

        try:
            async for event in self._stream:
                if self._cancelled:
                    log.debug("AudioStreamAdapter: cancelled, stopping")
                    break
                # LiveKit AudioFrameEvent has a .frame attribute of type AudioFrame
                frame = event.frame
                if frame and frame.data:
                    yield bytes(frame.data)
        except Exception as exc:
            log.error(
                "AudioStreamAdapter: error reading LiveKit audio stream",
                extra={"error": str(exc)[:200]},
            )
            raise


class AudioSinkAdapter:
    """
    Wraps a LiveKit AudioSource so TTS audio chunks can be pushed to the room.

    The TTS adapter (ElevenLabsTTSAdapter or MockTTSAdapter) yields raw 16-bit
    PCM chunks. This adapter wraps each chunk in a LiveKit AudioFrame and calls
    audio_source.capture_frame() to push audio to the room participant.

    Usage::

        sink = AudioSinkAdapter(livekit_audio_source)
        async for chunk in tts.synthesize_streaming(text):
            await sink.push(chunk)
    """

    def __init__(self, audio_source: object) -> None:
        """
        Args:
            audio_source: LiveKit AudioSource (has capture_frame method).
                          Type is object because livekit may not be importable.
        """
        self._source = audio_source

    async def push(self, pcm_chunk: bytes) -> None:
        """
        Push a raw PCM chunk to the LiveKit room.

        Wraps the bytes in a LiveKit AudioFrame with the correct format metadata.
        LiveKit's capture_frame is synchronous; we run it directly (not in executor)
        because it buffers internally and returns quickly.

        Args:
            pcm_chunk: 16-bit signed little-endian PCM at 16kHz mono.
                       Chunk size should be a multiple of BYTES_PER_FRAME (640 bytes)
                       for smooth playback, but any size is accepted.

        Does nothing if livekit-agents is not installed (test/offline mode).
        """
        if not _LIVEKIT_AVAILABLE or not self._source:
            return

        try:
            num_samples = len(pcm_chunk) // (NUM_CHANNELS * SAMPLE_WIDTH)
            if num_samples == 0:
                return

            frame = rtc.AudioFrame(  # type: ignore[call-arg]
                data=pcm_chunk,
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=num_samples,
            )
            await self._source.capture_frame(frame)
        except Exception as exc:
            log.error(
                "AudioSinkAdapter: error pushing audio frame",
                extra={"error": str(exc)[:200], "chunk_size": len(pcm_chunk)},
            )
            # Do not re-raise — audio push failure should not crash the call.
            # The caller continues and the TTS stream may partially play.


async def create_audio_source() -> tuple[object, object]:
    """
    Create a LiveKit AudioSource and publish it as a LocalAudioTrack.

    Returns:
        (audio_source, audio_track) — both LiveKit objects.
        In test/offline mode, returns (None, None).

    The track must be published to the room by the worker entrypoint using
    room.local_participant.publish_track(track).
    """
    if not _LIVEKIT_AVAILABLE:
        log.debug("create_audio_source: livekit not available, returning stubs")
        return None, None

    try:
        source = rtc.AudioSource(sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS)
        track = rtc.LocalAudioTrack.create_audio_track("agent-audio", source)
        return source, track
    except Exception as exc:
        log.error(
            "create_audio_source: failed to create LiveKit audio source",
            extra={"error": str(exc)[:200]},
        )
        return None, None
