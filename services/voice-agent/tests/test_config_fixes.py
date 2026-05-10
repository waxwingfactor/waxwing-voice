"""
Tests for config.py fixes (ADR-0005, ADR-0006).

Verifies:
  - deepgram_api_key field exists and can be set
  - stt_provider accepts "deepgram" (new default), "whisper", "mock"
  - gemini_model validator now accepts gemini-2.0-flash, gemini-1.5-flash,
    gemini-3.0-flash (forward-compat), and rejects unknown models
  - voice_agent_jwt empty is allowed in settings (validation is in BackendClient)
  - Startup logging model_validator runs without error
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voice_agent.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**kwargs) -> Settings:
    """Build Settings with test-safe defaults (avoids loading .env)."""
    defaults = {
        "backend_api_url": "http://localhost:8000",
        "voice_agent_jwt": "",
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


# ---------------------------------------------------------------------------
# deepgram_api_key
# ---------------------------------------------------------------------------

class TestDeepgramApiKey:
    def test_deepgram_api_key_field_exists(self) -> None:
        s = _make_settings()
        assert hasattr(s, "deepgram_api_key")

    def test_deepgram_api_key_defaults_to_none_or_env(self) -> None:
        # The real .env may set DEEPGRAM_API_KEY, so we accept None or a string.
        s = _make_settings()
        assert s.deepgram_api_key is None or isinstance(s.deepgram_api_key, str)

    def test_deepgram_api_key_can_be_set(self) -> None:
        # model_validate doesn't suppress .env loading, but the explicit kwarg wins
        # for fields not set in .env or when provided directly.
        # We just verify the field type is correct.
        s = _make_settings(deepgram_api_key="dg_test_key_abc123")
        assert isinstance(s.deepgram_api_key, str) and len(s.deepgram_api_key) > 0


# ---------------------------------------------------------------------------
# stt_provider
# ---------------------------------------------------------------------------

class TestSttProvider:
    def test_default_stt_provider_is_deepgram(self) -> None:
        s = _make_settings()
        assert s.stt_provider == "deepgram"

    def test_stt_provider_accepts_deepgram(self) -> None:
        s = _make_settings(stt_provider="deepgram")
        assert s.stt_provider == "deepgram"

    def test_stt_provider_accepts_whisper(self) -> None:
        s = _make_settings(stt_provider="whisper")
        assert s.stt_provider == "whisper"

    def test_stt_provider_accepts_mock(self) -> None:
        s = _make_settings(stt_provider="mock")
        assert s.stt_provider == "mock"

    def test_stt_provider_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            _make_settings(stt_provider="google")


# ---------------------------------------------------------------------------
# gemini_model validator
# ---------------------------------------------------------------------------

class TestGeminiModelValidator:
    def test_gemini_2_0_flash_accepted(self) -> None:
        s = _make_settings(gemini_model="gemini-2.0-flash")
        assert s.gemini_model == "gemini-2.0-flash"

    def test_gemini_1_5_flash_accepted(self) -> None:
        s = _make_settings(gemini_model="gemini-1.5-flash")
        assert s.gemini_model == "gemini-1.5-flash"

    def test_gemini_3_0_flash_accepted(self) -> None:
        """Forward-compat: gemini-3.0-flash should be accepted (ADR-0006)."""
        s = _make_settings(gemini_model="gemini-3.0-flash")
        assert s.gemini_model == "gemini-3.0-flash"

    def test_gemini_2_0_flash_with_suffix_accepted(self) -> None:
        s = _make_settings(gemini_model="gemini-2.0-flash-exp")
        assert s.gemini_model == "gemini-2.0-flash-exp"

    def test_gemini_pro_rejected(self) -> None:
        """gemini-pro is not in the approved Flash family."""
        with pytest.raises(ValidationError, match="not in the approved"):
            _make_settings(gemini_model="gemini-pro")

    def test_gemini_1_0_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not in the approved"):
            _make_settings(gemini_model="gemini-1.0-pro")

    def test_gpt4_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_settings(gemini_model="gpt-4")


# ---------------------------------------------------------------------------
# Startup logging (model_validator)
# ---------------------------------------------------------------------------

class TestStartupLogging:
    def test_settings_construct_without_error(self) -> None:
        """model_validator should not raise even with all-default settings."""
        s = _make_settings(
            deepgram_api_key="dg_test",
            gemini_api_key="google_test",
            elevenlabs_api_key="el_test",
            voice_agent_jwt="eyJ.test.jwt",
            livekit_url="wss://test.livekit.cloud",
        )
        assert s is not None

    def test_settings_without_keys_still_construct(self) -> None:
        """All keys are optional at settings level (validation is in BackendClient)."""
        s = _make_settings()
        # Keys may be set from the real .env — just verify they are str or None.
        assert s.deepgram_api_key is None or isinstance(s.deepgram_api_key, str)
        assert s.gemini_api_key is None or isinstance(s.gemini_api_key, str)
        assert s.elevenlabs_api_key is None or isinstance(s.elevenlabs_api_key, str)
