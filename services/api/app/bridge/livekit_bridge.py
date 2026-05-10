"""LiveKit bridge — converts Twilio mulaw audio to LiveKit PCM and back.

This module owns the LiveKit connection logic for one call's lifetime.
It is intentionally separate from twilio_webhooks.py so each concern
stays in its own module.

Architecture (ADR-0004):
    Twilio WS (/ws/twilio/media)
      → decode base64 mulaw 8kHz
      → resample to PCM 16kHz          (mulaw_to_pcm16k)
      → publish to LiveKit room as audio track
      ← subscribe to agent's audio track from LiveKit
      ← resample PCM 16kHz → mulaw 8kHz (pcm16k_to_mulaw)
      ← base64 encode → send back to Twilio WS

Graceful degradation:
    If the livekit or audioop packages are not installed, all public
    functions degrade safely — callers receive None / empty bytes / a
    warning log — so the WebSocket never crashes.

Consumer notes (Akhil — voice agent):
    - Room name format: "call-{call_id}"
    - Room metadata JSON matches the shape documented in ADR-0004.
    - Audio format sent to LiveKit: 16-bit signed LE PCM, 16 kHz, mono,
      640-byte frames (320 samples × 2 bytes = 20 ms per frame).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# audioop import — try stdlib first, fall back to audioop-lts backport
# ---------------------------------------------------------------------------

try:
    import audioop  # type: ignore[import-not-found]  # removed in Python 3.13
except ImportError:
    try:
        import audioop_lts as audioop  # type: ignore[import-not-found,no-redef]
    except ImportError:
        audioop = None  # type: ignore[assignment]
        logger.warning(
            "Neither 'audioop' nor 'audioop-lts' is available — "
            "audio conversion will be disabled.  "
            "Run `uv add audioop-lts` from services/api/ to enable it."
        )

# ---------------------------------------------------------------------------
# livekit imports — all guarded so the module loads even without the package
# ---------------------------------------------------------------------------

try:
    from livekit import rtc as lk_rtc  # type: ignore[import-not-found]

    _LIVEKIT_RTC_AVAILABLE = True
except ImportError:
    lk_rtc = None  # type: ignore[assignment]
    _LIVEKIT_RTC_AVAILABLE = False
    logger.warning(
        "livekit RTC package is not installed — LiveKit audio bridge will be disabled.  "
        "Run `uv add livekit` from services/api/ to enable it."
    )

try:
    from livekit.api import AccessToken, LiveKitAPI, VideoGrants  # type: ignore[import-not-found]

    _LIVEKIT_API_AVAILABLE = True
except ImportError:
    _LIVEKIT_API_AVAILABLE = False
    logger.warning(
        "livekit-api package is not installed — room management will be disabled.  "
        "Run `uv add livekit-api` from services/api/ to enable it."
    )


# ---------------------------------------------------------------------------
# Audio conversion helpers
# ---------------------------------------------------------------------------


def mulaw_to_pcm16k(mulaw_bytes: bytes) -> bytes:
    """Convert Twilio's mulaw 8 kHz audio to 16 kHz 16-bit signed PCM.

    The output is suitable for Akhil's voice agent worker (audio_io.py):
        sample_rate=16000, channels=1, sample_width=2 (16-bit LE).

    Args:
        mulaw_bytes: Raw G.711 mulaw bytes at 8 kHz from Twilio.

    Returns:
        16-bit signed little-endian PCM bytes at 16 kHz.
        Returns an empty bytes object if audioop is not available.
    """
    if audioop is None:
        return b""
    # Step 1: mulaw → 16-bit linear PCM at 8 kHz (sample_width=2)
    pcm_8k: bytes = audioop.ulaw2lin(mulaw_bytes, 2)
    # Step 2: resample 8 kHz → 16 kHz (mono, no state carried across calls)
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return pcm_16k


def pcm16k_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert LiveKit's 16 kHz 16-bit PCM back to Twilio mulaw 8 kHz.

    Args:
        pcm_bytes: 16-bit signed LE PCM bytes at 16 kHz from the agent.

    Returns:
        G.711 mulaw bytes at 8 kHz ready for Twilio's media stream.
        Returns an empty bytes object if audioop is not available.
    """
    if audioop is None:
        return b""
    # Step 1: resample 16 kHz → 8 kHz
    pcm_8k, _ = audioop.ratecv(pcm_bytes, 2, 1, 16000, 8000, None)
    # Step 2: 16-bit linear → mulaw
    return audioop.lin2ulaw(pcm_8k, 2)


# ---------------------------------------------------------------------------
# Room / token helpers
# ---------------------------------------------------------------------------


async def create_livekit_room(room_name: str, metadata: dict) -> str:
    """Create a LiveKit room with the given metadata.

    If the room already exists, LiveKit returns it without error.  If
    creation fails for any reason, a warning is logged and the room_name
    is returned so the caller can still attempt to connect.

    Akhil's worker expects the metadata JSON to contain:
        property_id, company_id, twilio_call_sid, caller_phone, livekit_room_id.

    Args:
        room_name: Unique room identifier, e.g. "call-{call_id}".
        metadata: Dict that will be serialised to JSON and stored on the room.

    Returns:
        The room_name (unchanged), for chaining convenience.
    """
    if not _LIVEKIT_API_AVAILABLE:
        logger.warning("livekit-api not available — skipping room creation for %s", room_name)
        return room_name

    settings = get_settings()
    metadata_json = json.dumps(metadata)

    try:
        async with LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        ) as lk_api:
            from livekit.api import CreateRoomRequest  # type: ignore[import-not-found]

            await lk_api.room.create_room(CreateRoomRequest(name=room_name, metadata=metadata_json))
            logger.info("LiveKit room created: room_name=%s", room_name)
    except Exception:
        logger.warning(
            "LiveKit room creation failed for room_name=%s — will attempt to join anyway",
            room_name,
            exc_info=True,
        )

    return room_name


def generate_participant_token(room_name: str, participant_identity: str) -> str:
    """Generate a LiveKit JWT for the bridge participant.

    Grants: RoomJoin, RoomPublish, RoomSubscribe.

    Args:
        room_name: The LiveKit room the token is scoped to.
        participant_identity: Participant identity string, e.g. "twilio-bridge".

    Returns:
        Signed JWT string.  Returns an empty string if livekit-api is not
        available or if credentials are not configured.
    """
    if not _LIVEKIT_API_AVAILABLE:
        logger.warning("livekit-api not available — cannot generate participant token")
        return ""

    settings = get_settings()

    if not settings.livekit_api_key or not settings.livekit_api_secret:
        logger.warning("LiveKit API key/secret not configured — cannot generate participant token")
        return ""

    try:
        grants = VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
        )
        token = (
            AccessToken(api_key=settings.livekit_api_key, api_secret=settings.livekit_api_secret)
            .with_identity(participant_identity)
            .with_grants(grants)
            .to_jwt()
        )
        return token
    except Exception:
        logger.warning(
            "Failed to generate LiveKit token for room=%s identity=%s",
            room_name,
            participant_identity,
            exc_info=True,
        )
        return ""


# ---------------------------------------------------------------------------
# TwilioLiveKitBridge — manages one call's bridge lifetime
# ---------------------------------------------------------------------------


class TwilioLiveKitBridge:
    """Manages the LiveKit connection for a single Twilio call.

    Lifecycle:
        bridge = TwilioLiveKitBridge(room_name, token, websocket, stream_sid)
        await bridge.start()   # connects, publishes caller track, subscribes to agent
        await bridge.push_caller_audio(mulaw_bytes)  # called per media event
        await bridge.stop()    # disconnects cleanly (called in finally block)

    Graceful degradation:
        If the livekit RTC package is absent or the connection fails, all
        methods no-op safely — the WebSocket continues to function and the
        call creates its DB record; audio is simply dropped.

    Args:
        room_name: LiveKit room name (e.g. "call-{call_id}").
        token: Signed JWT from generate_participant_token().
        twilio_websocket: The live Twilio WebSocket connection.
        stream_sid: Twilio stream SID used when sending audio back.
    """

    def __init__(
        self,
        room_name: str,
        token: str,
        twilio_websocket: WebSocket,
        stream_sid: str,
    ) -> None:
        self._room_name = room_name
        self._token = token
        self._twilio_ws = twilio_websocket
        self._stream_sid = stream_sid
        self._room: lk_rtc.Room | None = None
        self._audio_source: lk_rtc.AudioSource | None = None
        self._agent_loop_task: asyncio.Task | None = None
        self._frames_to_livekit: int = 0
        self._frames_to_twilio: int = 0

    async def start(self) -> None:
        """Connect to LiveKit room, publish the caller audio track, and subscribe to the agent.

        Sets up:
            - lk_rtc.Room connection
            - AudioSource + LocalAudioTrack for caller audio (caller → agent)
            - Background task reading agent audio and forwarding it to Twilio

        If anything fails, logs a warning and leaves the bridge in a disabled
        state so push_caller_audio and stop are safe no-ops.
        """
        if not _LIVEKIT_RTC_AVAILABLE:
            logger.warning(
                "livekit RTC not available — bridge disabled for room=%s", self._room_name
            )
            return

        if not self._token:
            logger.warning("No LiveKit token — bridge disabled for room=%s", self._room_name)
            return

        settings = get_settings()

        try:
            self._room = lk_rtc.Room()
            await self._room.connect(settings.livekit_url, self._token)
            logger.info("LiveKit bridge connected: room=%s", self._room_name)

            # Publish caller audio track (caller → agent)
            self._audio_source = lk_rtc.AudioSource(sample_rate=16000, num_channels=1)
            caller_track = lk_rtc.LocalAudioTrack.create_audio_track(
                "caller-audio", self._audio_source
            )
            publish_options = lk_rtc.TrackPublishOptions(
                source=lk_rtc.TrackSource.SOURCE_MICROPHONE
            )
            await self._room.local_participant.publish_track(caller_track, publish_options)
            logger.info(
                "PIPELINE[2/7] Caller audio track published to LiveKit: room=%s", self._room_name
            )

            # Subscribe to agent audio track and forward it back to Twilio
            self._agent_loop_task = asyncio.create_task(
                self._agent_to_twilio_loop(),
                name=f"agent-to-twilio-{self._room_name}",
            )
        except Exception:
            logger.warning(
                "LiveKit bridge failed to start for room=%s — audio bridging disabled",
                self._room_name,
                exc_info=True,
            )
            self._room = None
            self._audio_source = None

    async def push_caller_audio(self, mulaw_bytes: bytes) -> None:
        """Convert mulaw→PCM16k and push to the LiveKit room.

        Splits the converted PCM into 640-byte frames (20 ms at 16 kHz) and
        pushes each frame to the AudioSource so the agent receives a clean,
        consistent frame size matching audio_io.py's expectations.

        Args:
            mulaw_bytes: Raw G.711 mulaw bytes from a Twilio media event.
        """
        if self._audio_source is None or audioop is None:
            return

        try:
            pcm = mulaw_to_pcm16k(mulaw_bytes)
            if not pcm:
                return

            # Chunk PCM into 640-byte frames (320 samples × 2 bytes = 20 ms)
            frame_size = 640
            for offset in range(0, len(pcm), frame_size):
                chunk = pcm[offset : offset + frame_size]
                if len(chunk) < frame_size:
                    # Pad the last partial frame with silence to maintain frame alignment
                    chunk = chunk + b"\x00" * (frame_size - len(chunk))
                frame = lk_rtc.AudioFrame(
                    data=chunk,
                    sample_rate=16000,
                    num_channels=1,
                    samples_per_channel=320,
                )
                await self._audio_source.capture_frame(frame)
                self._frames_to_livekit += 1
                if self._frames_to_livekit == 1:
                    logger.info(
                        "PIPELINE[2/7] First PCM frame captured to LiveKit: room=%s",
                        self._room_name,
                    )
                elif self._frames_to_livekit % 500 == 0:
                    logger.debug(
                        "PIPELINE bridge→LiveKit frames=%d room=%s",
                        self._frames_to_livekit, self._room_name,
                    )
        except Exception:
            logger.warning(
                "Error pushing caller audio to LiveKit for room=%s",
                self._room_name,
                exc_info=True,
            )

    async def stop(self) -> None:
        """Disconnect from the LiveKit room cleanly.

        Cancels the agent-to-Twilio background task and disconnects the room.
        Safe to call even if start() never completed successfully.
        """
        if self._agent_loop_task is not None and not self._agent_loop_task.done():
            self._agent_loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._agent_loop_task

        if self._room is not None:
            try:
                await self._room.disconnect()
                logger.info("LiveKit bridge stopped: room=%s", self._room_name)
            except Exception:
                logger.warning(
                    "Error disconnecting from LiveKit room=%s",
                    self._room_name,
                    exc_info=True,
                )
            self._room = None

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    async def _agent_to_twilio_loop(self) -> None:
        """Read agent audio frames from LiveKit and forward them to Twilio.

        Runs as a background asyncio task for the duration of the call.
        Converts 16 kHz PCM → mulaw 8 kHz, base64-encodes it, and sends
        the JSON media event back on the Twilio WebSocket.

        Terminates cleanly on CancelledError or when the room disconnects.
        """
        if self._room is None:
            return

        # LiveKit SDK uses a callback registration API (not async generator).
        # Bridge events into an asyncio.Queue so this async task can await them.
        track_queue: asyncio.Queue = asyncio.Queue()

        @self._room.on("track_subscribed")
        def _on_track_subscribed(track, publication, participant):  # noqa: ANN001
            if isinstance(track, lk_rtc.RemoteAudioTrack):
                track_queue.put_nowait(track)

        try:
            while True:
                track = await track_queue.get()
                logger.info(
                    "Agent audio track subscribed for room=%s — forwarding to Twilio",
                    self._room_name,
                )
                audio_stream = lk_rtc.AudioStream(track)
                async for frame_event in audio_stream:
                    try:
                        pcm = bytes(frame_event.frame.data)
                        mulaw = pcm16k_to_mulaw(pcm)
                        if not mulaw:
                            continue
                        payload = base64.b64encode(mulaw).decode("ascii")
                        await self._twilio_ws.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": self._stream_sid,
                                    "media": {"payload": payload},
                                }
                            )
                        )
                        self._frames_to_twilio += 1
                        if self._frames_to_twilio == 1:
                            logger.info(
                                "PIPELINE[6/7] First TTS frame forwarded to Twilio: room=%s",
                                self._room_name,
                            )
                        elif self._frames_to_twilio % 500 == 0:
                            logger.debug(
                                "PIPELINE LiveKit→Twilio frames=%d room=%s",
                                self._frames_to_twilio, self._room_name,
                            )
                    except Exception:
                        logger.warning(
                            "Error forwarding agent audio to Twilio for room=%s",
                            self._room_name,
                            exc_info=True,
                        )
        except asyncio.CancelledError:
            logger.info("Agent-to-Twilio loop cancelled for room=%s", self._room_name)
            raise
        except Exception:
            logger.warning(
                "Agent-to-Twilio loop error for room=%s",
                self._room_name,
                exc_info=True,
            )
