"""
Waxwing Voice Agent

Real-time voice pipeline for property management.
Stack: Twilio (telephony) -> LiveKit Agents -> Whisper (STT)
       -> Gemini-3.0 Flash (LLM) -> VibeVoice (TTS)

All durable actions go through Harsha's backend API.
No direct database writes from this service.
"""

__version__ = "0.1.0"
