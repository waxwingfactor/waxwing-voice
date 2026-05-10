"""
Twilio → LiveKit audio bridge.

This package owns the real-time audio relay between Twilio's Media Streams
WebSocket and a per-call LiveKit room.

Entry points:
    LiveKitBridge — instantiated once per Twilio WebSocket connection.
                    Call await bridge.handle_start(data) on the Twilio "start" event,
                    await bridge.handle_media(data) on each "media" event,
                    and await bridge.close() on disconnect or "stop".

Audio codec utilities:
    audio_codec   — pure-Python mu-law encode/decode tables and numpy-based
                    resampler for 8 kHz ↔ 16 kHz conversion.

Owner: Akhil (services/voice-agent/) wrote this layer; Harsha (services/api/)
hosts it. Any changes to the bridge's LiveKit room creation API must be
coordinated between both owners.
"""

from app.bridge.livekit_bridge import LiveKitBridge

__all__ = ["LiveKitBridge"]
