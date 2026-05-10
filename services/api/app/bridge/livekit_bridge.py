"""
Twilio Media Streams ↔ LiveKit room bridge.

This module implements the real-time audio relay between Twilio's bidirectional
Media Streams WebSocket and a per-call LiveKit room. One LiveKitBridge instance
is created per Twilio WebSocket connection (i.e., per call).

Architecture (ADR-0004, ADR-0006):
  Twilio caller audio (PCMU 8 kHz)
    → WebSocket "media" events → LiveKitBridge.handle_media()
    → mu-law decode + 8kHz→16kHz upsample (audio_codec.py)
    → LiveKit room: publish to "caller-audio" LocalAudioTrack
    → VoicePipelineAgent (services/voice-agent/) receives 16kHz PCM

  VoicePipelineAgent TTS output (16kHz PCM)
    → LiveKit room: subscribed "agent-audio" track
    → LiveKitBridge: track data callback
    → 16kHz→8kHz downsample + mu-law encode (audio_codec.py)
    → Twilio WebSocket: send "media" event with base64 PCMU
    → caller's phone

Bridge lifecycle:
  1. Twilio opens WebSocket → twilio_media_stream() accepts
  2. "connected" event → log
  3. "start" event → LiveKitBridge.handle_start()
       - Create LiveKit room via livekit-server-sdk (room name = call_id UUID)
       - Set room metadata: {"call_id": uuid, "twilio_call_sid": "CA...",
                            "caller_phone": "+1...", "property_id": uuid}
       - Join room as participant identity "twilio-bridge"
       - Create LocalAudioTrack "caller-audio" and publish it
       - Subscribe to agent's "agent-audio" track for return path
  4. "media" event → LiveKitBridge.handle_media()
       - Decode base64 payload → PCMU bytes
       - audio_codec.mulaw_to_pcm16k() → 16kHz PCM bytes
       - Wrap in rtc.AudioFrame and capture to LocalAudioTrack
  5. Agent produces TTS audio:
       - Bridge receives via subscribed track data callback
       - audio_codec.pcm16k_to_mulaw() → PCMU bytes
       - base64-encode → send Twilio "media" JSON message
  6. "stop" event or WebSocket disconnect → LiveKitBridge.close()
       - Disconnect from LiveKit room
       - WebSocket is closed by caller

No audioop. See audio_codec.py for the pure-Python + numpy implementation.

Backpressure: handle_media() is a coroutine. The FastAPI WebSocket event loop
dispatches one message at a time. Heavy audio processing (decode + upsample) is
fast enough (~0.1 ms per 160-sample chunk) that it does not block the event loop
measurably. LiveKit track capture is fire-and-forget (internal buffering).

Owner: Akhil (voice pipeline). Hosted in services/api/ because the Twilio
WebSocket server lives there. All LiveKit SDK calls go through this module.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from app.bridge.audio_codec import mulaw_to_pcm16k, pcm16k_to_mulaw

log = logging.getLogger("api.bridge.livekit_bridge")

# Guard against missing livekit SDK during offline / test runs.
try:
    from livekit import api as lk_api
    from livekit import rtc as lk_rtc
    _LIVEKIT_AVAILABLE = True
except ImportError:
    lk_api = None  # type: ignore[assignment]
    lk_rtc = None  # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False
    log.warning(
        "livekit package not installed — LiveKitBridge will run in stub mode. "
        "Run `uv add livekit-server-sdk livekit` from services/api/ to enable."
    )

# Audio format constants (must match voice-agent worker expectations).
_CALLER_AUDIO_SAMPLE_RATE = 16_000  # Hz — after upsampling from Twilio's 8 kHz
_CALLER_AUDIO_CHANNELS = 1          # mono
_CALLER_AUDIO_SAMPLE_WIDTH = 2      # bytes (16-bit PCM)

# LiveKit audio frame duration: 20 ms at 16 kHz = 320 samples = 640 bytes.
_FRAME_DURATION_MS = 20
_SAMPLES_PER_FRAME = (_CALLER_AUDIO_SAMPLE_RATE * _FRAME_DURATION_MS) // 1000  # 320
_BYTES_PER_FRAME = _SAMPLES_PER_FRAME * _CALLER_AUDIO_CHANNELS * _CALLER_AUDIO_SAMPLE_WIDTH  # 640

# Bridge participant identity (as seen by the voice-agent worker in the room).
_BRIDGE_IDENTITY = "twilio-bridge"

# Track names — voice-agent subscribes to "caller-audio"; bridge subscribes to "agent-audio".
_CALLER_TRACK_NAME = "caller-audio"
_AGENT_TRACK_NAME = "agent-audio"


class LiveKitBridge:
    """
    Bridges one Twilio Media Streams WebSocket to one LiveKit room.

    Create one instance per inbound call. Call handle_start() on the Twilio
    "start" event, handle_media() on each "media" event, and close() when the
    connection ends.

    The bridge:
      - Creates the LiveKit room and sets metadata (call_id, twilio_call_sid, etc.)
      - Publishes caller audio as a LocalAudioTrack named "caller-audio"
      - Subscribes to the agent's "agent-audio" track and forwards PCM back to
        Twilio as PCMU, sending Twilio "media" WebSocket messages

    Usage::

        bridge = LiveKitBridge(
            livekit_url=settings.livekit_url,
            livekit_api_key=settings.livekit_api_key,
            livekit_api_secret=settings.livekit_api_secret,
        )
        await bridge.handle_start(start_event_data, websocket)
        # Then in a loop:
        await bridge.handle_media(media_event_data)
        # Finally:
        await bridge.close()
    """

    def __init__(
        self,
        livekit_url: str,
        livekit_api_key: str,
        livekit_api_secret: str,
    ) -> None:
        """
        Args:
            livekit_url:        wss://... LiveKit server URL.
            livekit_api_key:    LiveKit API key (from settings).
            livekit_api_secret: LiveKit API secret (from settings, never logged).
        """
        self._livekit_url = livekit_url
        self._api_key = livekit_api_key
        self._api_secret = livekit_api_secret  # secret — never log this value

        # Set on handle_start()
        self._room_name: str | None = None
        self._call_id: str | None = None
        self._twilio_call_sid: str | None = None
        self._stream_sid: str | None = None
        self._caller_phone: str | None = None
        self._property_id: str | None = None

        # LiveKit room and audio source (set on connect)
        self._room: Any = None          # lk_rtc.Room | None
        self._audio_source: Any = None  # lk_rtc.AudioSource | None
        self._local_track: Any = None   # lk_rtc.LocalAudioTrack | None

        # Websocket for sending agent audio back to Twilio
        self._websocket: Any = None

        # PCM buffer: accumulate partial frames before capturing to LiveKit.
        # LiveKit capture_frame works with any size, but we batch to avoid
        # excessive per-packet overhead.
        self._pcm_buffer = bytearray()

        # Return-path task: reads agent audio and sends to Twilio
        self._agent_audio_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    async def handle_start(
        self,
        data: dict[str, Any],
        websocket: Any,
        *,
        call_id: str,
        property_id: str,
        caller_phone: str | None = None,
    ) -> None:
        """
        Called when Twilio sends the "start" event.

        Extracts stream metadata, creates the LiveKit room, joins as the bridge
        participant, and publishes the caller audio track.

        Args:
            data:         The parsed "start" event dict from Twilio.
            websocket:    The FastAPI WebSocket — held for sending agent audio back.
            call_id:      Internal call UUID (from the webhook-created Call record).
            property_id:  Property UUID for this call.
            caller_phone: Caller's E.164 phone number (PII — not logged).
        """
        start_payload = data.get("start", {})
        self._stream_sid = start_payload.get("streamSid", "")
        self._twilio_call_sid = start_payload.get("callSid", "")
        self._call_id = call_id
        self._property_id = property_id
        self._caller_phone = caller_phone
        self._websocket = websocket

        # Room name = call_id UUID — unique per call, matches the backend Call.id
        self._room_name = call_id

        log.info(
            "Bridge: start event received",
            extra={
                "stream_sid": self._stream_sid,
                "twilio_call_sid": self._twilio_call_sid,
                "call_id": call_id,
                # caller_phone deliberately omitted (PII)
            },
        )

        if not _LIVEKIT_AVAILABLE:
            log.warning("Bridge: LiveKit SDK not available — running in stub mode")
            return

        await self._connect_to_livekit()

    async def handle_media(self, data: dict[str, Any]) -> None:
        """
        Called for each Twilio "media" event (one per ~20ms audio packet).

        Decodes the base64 PCMU payload, converts to 16 kHz PCM, and captures
        to the LiveKit audio source so the voice-agent worker can transcribe it.

        Args:
            data: The parsed "media" event dict from Twilio.
        """
        if not _LIVEKIT_AVAILABLE or self._audio_source is None:
            return

        try:
            media_payload = data.get("media", {})
            b64_payload: str = media_payload.get("payload", "")
            if not b64_payload:
                return

            mulaw_bytes = base64.b64decode(b64_payload)

            # Decode mu-law and upsample 8kHz → 16kHz
            pcm_16k_bytes = mulaw_to_pcm16k(mulaw_bytes)

            # Accumulate into buffer then capture frames
            self._pcm_buffer.extend(pcm_16k_bytes)
            await self._flush_pcm_buffer()

        except Exception as exc:
            log.error(
                "Bridge: error processing media event",
                extra={"error": str(exc)[:200]},
            )

    async def handle_dtmf(self, data: dict[str, Any]) -> None:
        """
        Called when Twilio sends a "dtmf" event.

        DTMF digits are logged for observability. Future: route to the voice agent
        so it can react to keypad input (e.g., "press 1 for leasing").

        Args:
            data: The parsed "dtmf" event dict.
        """
        digit = data.get("dtmf", {}).get("digit", "")
        log.info("Bridge: DTMF received", extra={"digit": digit, "call_id": self._call_id})

    async def close(self) -> None:
        """
        Clean up the bridge: cancel the agent audio return-path task and
        disconnect from the LiveKit room.

        Safe to call multiple times (idempotent).
        """
        log.info("Bridge: closing", extra={"call_id": self._call_id})

        if self._agent_audio_task and not self._agent_audio_task.done():
            self._agent_audio_task.cancel()
            try:
                await self._agent_audio_task
            except asyncio.CancelledError:
                pass

        if self._room is not None and _LIVEKIT_AVAILABLE:
            try:
                await self._room.disconnect()
            except Exception as exc:
                log.warning(
                    "Bridge: error disconnecting from LiveKit room",
                    extra={"error": str(exc)[:200]},
                )
            self._room = None

        self._audio_source = None
        self._local_track = None
        log.info("Bridge: closed", extra={"call_id": self._call_id})

    # ------------------------------------------------------------------
    # Private: LiveKit connection
    # ------------------------------------------------------------------

    async def _connect_to_livekit(self) -> None:
        """
        Create the LiveKit room, join as bridge participant, publish caller track,
        and subscribe to the agent's audio track for the return path.
        """
        if not _LIVEKIT_AVAILABLE:
            return

        try:
            # Step 1: Create the LiveKit room via server API.
            # Room metadata carries the call context that the voice-agent reads.
            # Medium 3 fix: Include caller_phone in room metadata.
            # The prior code omitted it as PII. However, caller_phone is essential
            # for lead qualification (the worker stores it on CallState.caller_phone_number).
            # LiveKit room metadata is encrypted in transit (TLS) and is only readable
            # by participants that successfully join the room (i.e., the dispatched
            # voice worker). It is NOT logged by the bridge or the worker — only stored
            # on the in-memory CallState and persisted via create_call() to the backend.
            metadata = json.dumps({
                "call_id": self._call_id,
                "twilio_call_sid": self._twilio_call_sid,
                "property_id": self._property_id,
                "caller_phone": self._caller_phone or "",  # PII — encrypted in transit; not logged
                "livekit_room_id": self._room_name,
            })

            # Bug 5 fix: use async context manager so the underlying aiohttp
            # ClientSession is properly closed after the room creation API call.
            # Without this, each call leaks an aiohttp session (one per call).
            room_options = lk_api.CreateRoomRequest(
                name=self._room_name,
                metadata=metadata,
                empty_timeout=300,   # 5 minutes — end room if participants leave
                max_participants=10,
            )
            async with lk_api.LiveKitAPI(
                url=self._livekit_url,
                api_key=self._api_key,
                api_secret=self._api_secret,
            ) as lk_client:
                try:
                    await lk_client.room.create_room(room_options)
                    log.info(
                        "Bridge: LiveKit room created",
                        extra={"room_name": self._room_name},
                    )
                except Exception as exc:
                    # Room may already exist if there was a reconnect; log and continue.
                    log.warning(
                        "Bridge: room create returned error (may already exist)",
                        extra={"error": str(exc)[:200], "room_name": self._room_name},
                    )

            # Step 2: Generate a participant token for the bridge.
            token = (
                lk_api.AccessToken(
                    api_key=self._api_key,
                    api_secret=self._api_secret,
                )
                .with_identity(_BRIDGE_IDENTITY)
                .with_name("Twilio Bridge")
                .with_grants(
                    lk_api.VideoGrants(
                        room_join=True,
                        room=self._room_name,
                        can_publish=True,
                        can_subscribe=True,
                    )
                )
                .to_jwt()
            )

            # Step 3: Connect the room.
            self._room = lk_rtc.Room()

            # Register track subscription callback BEFORE connecting to avoid the
            # track subscription race (issue #4 from prior diagnostics).
            self._room.on("track_subscribed", self._on_track_subscribed)
            self._room.on("disconnected", self._on_room_disconnected)

            await self._room.connect(self._livekit_url, token)
            log.info(
                "Bridge: connected to LiveKit room",
                extra={"room_name": self._room_name, "identity": _BRIDGE_IDENTITY},
            )

            # Step 4: Create and publish the caller audio track.
            self._audio_source = lk_rtc.AudioSource(
                sample_rate=_CALLER_AUDIO_SAMPLE_RATE,
                num_channels=_CALLER_AUDIO_CHANNELS,
            )
            self._local_track = lk_rtc.LocalAudioTrack.create_audio_track(
                _CALLER_TRACK_NAME, self._audio_source
            )
            options = lk_rtc.TrackPublishOptions(source=lk_rtc.TrackSource.SOURCE_MICROPHONE)
            await self._room.local_participant.publish_track(self._local_track, options)
            log.info(
                "Bridge: caller-audio track published",
                extra={"room_name": self._room_name},
            )

            # Bug C fix: schedule a 5-second warning if no agent track is subscribed.
            # If _agent_audio_task is still None after 5 s, the voice worker likely
            # failed to connect or published a track the filter didn't recognise.
            asyncio.create_task(
                self._warn_if_no_agent_track(),
                name=f"agent-track-timeout-{self._call_id}",
            )

        except Exception as exc:
            log.error(
                "Bridge: failed to connect to LiveKit",
                extra={"error": str(exc)[:200], "room_name": self._room_name},
            )
            # Bridge continues in stub mode — audio will not reach the agent.
            # The call can still be tracked for observability; a future reconnect
            # mechanism could attempt recovery here.

    def _on_room_disconnected(self, reason: Any = None) -> None:
        """
        Called when the LiveKit room disconnects unexpectedly.

        Logs the disconnect for observability. Reconnect on transient failures is
        not implemented in Phase 1 (issue #9 from prior diagnostics — tracked in
        BLOCKERS.md as a future improvement).
        """
        log.warning(
            "Bridge: LiveKit room disconnected",
            extra={"call_id": self._call_id, "reason": str(reason)[:200]},
        )

    # ------------------------------------------------------------------
    # Private: agent audio return path
    # ------------------------------------------------------------------

    def _on_track_subscribed(
        self,
        track: Any,
        publication: Any,
        participant: Any,
    ) -> None:
        """
        Called when a remote participant publishes an audio track.

        Accepts the first audio track from any participant that is not the bridge
        itself (identity != _BRIDGE_IDENTITY). This is the agent's TTS output.

        Bug C fix: The prior filter required track.name == "agent-audio".
        VoicePipelineAgent publishes TTS tracks with a framework-assigned name
        (e.g. "voice-agent-audio") that does NOT match that constant, so the
        bridge never started the return path — callers heard silence.

        Fix: Drop the name check. Filter on:
          1. isinstance(track, RemoteAudioTrack) — audio only, no video
          2. participant.identity != _BRIDGE_IDENTITY — not our own loopback
        Since the voice worker is the only non-bridge participant, this is reliable.
        """
        # Bug 3 fix (retained): isinstance check works across SDK versions.
        is_audio = (
            isinstance(track, lk_rtc.RemoteAudioTrack)
            if _LIVEKIT_AVAILABLE
            else False
        )
        # Bug C fix: accept audio from any non-bridge participant; drop name check.
        is_from_agent = (
            hasattr(participant, "identity")
            and participant.identity != _BRIDGE_IDENTITY
        )
        if is_audio and is_from_agent:
            log.info(
                "Bridge: subscribed to agent audio track",
                extra={
                    "call_id": self._call_id,
                    "participant": str(participant.identity),
                    "track_name": getattr(track, "name", "<unknown>"),
                },
            )
            if self._agent_audio_task is None or self._agent_audio_task.done():
                self._agent_audio_task = asyncio.create_task(
                    self._forward_agent_audio(track),
                    name=f"agent-audio-{self._call_id}",
                )

    async def _warn_if_no_agent_track(self) -> None:
        """
        Bug C fix: warn if no agent audio track is subscribed within 5 seconds.

        A missing agent track means the voice worker either failed to connect or
        published a track that the subscription filter did not accept. This warning
        makes the mis-coordination visible in logs without crashing the bridge.
        """
        await asyncio.sleep(5.0)
        if self._agent_audio_task is None or self._agent_audio_task.done():
            log.warning(
                "Bridge: no agent audio track subscribed within 5 seconds of room connect. "
                "Caller will hear silence. Check that the voice worker is running and "
                "has joined room '%s'.",
                self._room_name,
                extra={
                    "call_id": self._call_id,
                    "room_name": self._room_name,
                },
            )

    async def _forward_agent_audio(self, track: Any) -> None:
        """
        Read PCM frames from the agent's audio track and send them to Twilio.

        Runs as a background task for the duration of the call. Cancellation
        is handled in close().
        """
        if not _LIVEKIT_AVAILABLE or self._websocket is None:
            return

        log.info("Bridge: agent audio forward task started", extra={"call_id": self._call_id})

        try:
            # Bug B fix: pin sample_rate=16000 and num_channels=1 so the LiveKit SDK
            # resamples TTS output (which may arrive at 24 kHz or 48 kHz from ElevenLabs)
            # to the rate our codec expects. Without this, pcm16k_to_mulaw() receives
            # frames at the track's native rate and produces pitch-shifted (chipmunk) audio.
            audio_stream = lk_rtc.AudioStream(track, sample_rate=16000, num_channels=1)
            async for audio_frame_event in audio_stream:
                frame = audio_frame_event.frame
                if not frame or not frame.data:
                    continue

                pcm_bytes = bytes(frame.data)

                # Downsample 16kHz → 8kHz and encode to mu-law
                mulaw_bytes = pcm16k_to_mulaw(pcm_bytes)
                b64_payload = base64.b64encode(mulaw_bytes).decode("ascii")

                # Send Twilio "media" message
                message = json.dumps({
                    "event": "media",
                    "streamSid": self._stream_sid,
                    "media": {"payload": b64_payload},
                })
                try:
                    await self._websocket.send_text(message)
                except Exception as ws_exc:
                    log.warning(
                        "Bridge: failed to send agent audio to Twilio WebSocket",
                        extra={"error": str(ws_exc)[:200]},
                    )
                    break  # WebSocket likely closed; stop the return path

        except asyncio.CancelledError:
            log.debug("Bridge: agent audio task cancelled", extra={"call_id": self._call_id})
            raise
        except Exception as exc:
            log.error(
                "Bridge: agent audio forward error",
                extra={"call_id": self._call_id, "error": str(exc)[:200]},
            )

    # ------------------------------------------------------------------
    # Private: caller audio delivery to LiveKit
    # ------------------------------------------------------------------

    async def _flush_pcm_buffer(self) -> None:
        """
        Deliver accumulated PCM bytes to the LiveKit AudioSource as audio frames.

        Consumes complete 20ms frames from the buffer; any partial frame is left
        for the next call. This prevents clicks from misaligned frame boundaries.
        """
        if self._audio_source is None:
            return

        while len(self._pcm_buffer) >= _BYTES_PER_FRAME:
            chunk = bytes(self._pcm_buffer[:_BYTES_PER_FRAME])
            del self._pcm_buffer[:_BYTES_PER_FRAME]

            try:
                frame = lk_rtc.AudioFrame(
                    data=chunk,
                    sample_rate=_CALLER_AUDIO_SAMPLE_RATE,
                    num_channels=_CALLER_AUDIO_CHANNELS,
                    samples_per_channel=_SAMPLES_PER_FRAME,
                )
                await self._audio_source.capture_frame(frame)
            except Exception as exc:
                log.error(
                    "Bridge: error capturing audio frame",
                    extra={"error": str(exc)[:200]},
                )
                # Do not re-raise: a missed frame causes a click, not a crash.
                break
