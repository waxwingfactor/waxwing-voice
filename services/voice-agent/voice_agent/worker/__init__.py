"""
LiveKit Agents worker for Waxwing Voice.

Architecture: Twilio → LiveKit bridge (ADR-0004).
  - Harsha's backend bridges Twilio Media Streams into a per-call LiveKit room.
  - This worker joins the room as a LiveKit Agents participant.
  - Audio I/O is via LiveKit AudioStream (in) and AudioSource (out).
  - VoiceSession orchestrates the conversation pipeline.

Entry point: voice_agent/worker/entrypoint.py
Audio I/O:   voice_agent/worker/audio_io.py
"""
