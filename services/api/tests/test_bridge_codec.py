"""
Bridge audio codec unit tests.

Tests for services/api/app/bridge/audio_codec.py.
No database, no FastAPI app, no LiveKit SDK — pure codec math.

Covers:
  - mu-law decode table correctness (spot-check known G.711 values)
  - mu-law encode round-trip (encode then decode stays close)
  - 8kHz → 16kHz upsample (length doubles, sample values replicated)
  - 16kHz → 8kHz downsample (length halves, even-indexed samples kept)
  - mulaw_to_pcm16k() combined convenience function
  - pcm16k_to_mulaw() combined convenience function
  - Round-trip: silence passes through cleanly
  - Round-trip: non-trivial audio is reconstructed within acceptable SNR for telephony

These tests run without a database and do not require the FastAPI app to be running.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.bridge.audio_codec import (
    MULAW_DECODE_TABLE,
    downsample_16k_to_8k,
    mulaw_decode,
    mulaw_encode,
    mulaw_to_pcm16k,
    pcm16k_to_mulaw,
    upsample_8k_to_16k,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _silence_mulaw(n_samples: int = 160) -> bytes:
    """Generate n_samples of silence as mu-law bytes (0x7F = +0 in G.711)."""
    # 0xFF encodes silence (0) in mu-law because G.711 inverts all bits.
    return bytes([0xFF] * n_samples)


def _sine_pcm_8k(freq_hz: float = 440.0, n_samples: int = 160) -> np.ndarray:
    """Generate a sine wave at 8 kHz as int16 PCM samples."""
    t = np.arange(n_samples) / 8000.0
    wave = np.sin(2 * np.pi * freq_hz * t)
    return (wave * 16383).astype(np.int16)  # half scale to avoid clipping


# ---------------------------------------------------------------------------
# Decode table
# ---------------------------------------------------------------------------


class TestMulawDecodeTable:
    def test_table_has_256_entries(self) -> None:
        assert len(MULAW_DECODE_TABLE) == 256

    def test_all_entries_are_int16_range(self) -> None:
        for v in MULAW_DECODE_TABLE:
            assert -32768 <= v <= 32767, f"Out of int16 range: {v}"

    def test_silence_byte_decodes_near_zero(self) -> None:
        # 0xFF is the standard mu-law silence byte (encodes ~0 PCM).
        # Tight tolerance: with the correct ITU-T G.711 formula, 0xFF decodes to
        # exactly 0. The prior buggy formula decoded it to -29.
        val = MULAW_DECODE_TABLE[0xFF]
        assert abs(val) < 8, f"Silence byte decoded to {val}, expected 0 (within quantisation step)"

    def test_decode_is_not_all_zeros(self) -> None:
        # Make sure the table isn't trivially all-zero (sanity check).
        nonzero = sum(1 for v in MULAW_DECODE_TABLE if v != 0)
        assert nonzero > 200, "Decode table is mostly zeros — likely a bug"

    # Test improvement A: G.711 reference value assertions (prevents codec regression)
    def test_mulaw_decode_matches_g711_reference_values(self) -> None:
        """
        Spot-check against ITU-T G.711 reference values.

        These exact values are mandated by the standard. If any of these fail,
        the decode table formula is wrong — not a tolerance issue.

        Reference: ITU-T G.711 (1988) Annex A, verified against Sun/SOX ulaw2linear.
          - 0x00: maximum negative value (-32124)
          - 0xFF: silence / zero (0)
          - 0x80: maximum positive value (+32124)
        """
        assert MULAW_DECODE_TABLE[0x00] == -32124, (
            f"0x00 decoded to {MULAW_DECODE_TABLE[0x00]}, expected -32124"
        )
        assert MULAW_DECODE_TABLE[0xFF] == 0, (
            f"0xFF decoded to {MULAW_DECODE_TABLE[0xFF]}, expected 0"
        )
        assert MULAW_DECODE_TABLE[0x80] == 32124, (
            f"0x80 decoded to {MULAW_DECODE_TABLE[0x80]}, expected 32124"
        )


# ---------------------------------------------------------------------------
# Mu-law decode
# ---------------------------------------------------------------------------


class TestMulawDecode:
    def test_output_dtype_is_int16(self) -> None:
        buf = _silence_mulaw(100)
        result = mulaw_decode(buf)
        assert result.dtype == np.int16

    def test_output_length_matches_input(self) -> None:
        n = 160
        result = mulaw_decode(_silence_mulaw(n))
        assert len(result) == n

    def test_silence_decodes_near_zero(self) -> None:
        result = mulaw_decode(_silence_mulaw(160))
        assert np.all(np.abs(result) < 100)

    def test_accepts_bytearray(self) -> None:
        buf = bytearray(_silence_mulaw(10))
        result = mulaw_decode(buf)
        assert len(result) == 10

    def test_all_256_byte_values_decode_without_error(self) -> None:
        all_bytes = bytes(range(256))
        result = mulaw_decode(all_bytes)
        assert len(result) == 256


# ---------------------------------------------------------------------------
# Mu-law encode
# ---------------------------------------------------------------------------


class TestMulawEncode:
    def test_output_is_bytes(self) -> None:
        samples = np.zeros(100, dtype=np.int16)
        result = mulaw_encode(samples)
        assert isinstance(result, bytes)

    def test_output_length_matches_input(self) -> None:
        samples = _sine_pcm_8k(n_samples=160)
        result = mulaw_encode(samples)
        assert len(result) == 160

    def test_silence_encodes_consistently(self) -> None:
        silence = np.zeros(100, dtype=np.int16)
        result = mulaw_encode(silence)
        # All silence samples should encode to the same byte value.
        unique_values = set(result)
        assert len(unique_values) == 1, f"Silence encodes to multiple values: {unique_values}"

    def test_positive_and_negative_samples_encode_differently(self) -> None:
        pos = np.array([10000], dtype=np.int16)
        neg = np.array([-10000], dtype=np.int16)
        assert mulaw_encode(pos) != mulaw_encode(neg)

    # Test improvement A: encode reference values (prevents sign-inversion regression)
    def test_mulaw_encode_silence_is_0xFF(self) -> None:
        """
        Mu-law byte 0xFF represents silence (PCM ≈ 0).

        The prior buggy implementation encoded silence as 0x3E due to an inverted
        sign bit and wrong exponent mask. With the correct ITU-T G.711 formula,
        silence (int16 = 0) must encode to 0xFF.
        """
        encoded = mulaw_encode(np.zeros(8, dtype=np.int16))
        assert all(b == 0xFF for b in encoded), (
            f"Silence should encode to 0xFF, got {[hex(b) for b in encoded]}"
        )

    def test_mulaw_encode_max_positive_is_0x80(self) -> None:
        """
        Maximum positive PCM value (32767) encodes to 0x80 in G.711.

        0x80 is the largest positive mu-law value; 0x00 is the most negative.
        """
        encoded = mulaw_encode(np.array([32767], dtype=np.int16))
        assert encoded[0] == 0x80, (
            f"Max positive sample should encode to 0x80, got 0x{encoded[0]:02X}"
        )

    def test_mulaw_encode_max_negative_is_0x00(self) -> None:
        """Maximum negative PCM value (-32767) encodes to 0x00 in G.711."""
        encoded = mulaw_encode(np.array([-32767], dtype=np.int16))
        assert encoded[0] == 0x00, (
            f"Max negative sample should encode to 0x00, got 0x{encoded[0]:02X}"
        )


# ---------------------------------------------------------------------------
# Encode-decode round trip
# ---------------------------------------------------------------------------


class TestEncodeDecodeRoundTrip:
    """
    Mu-law is a lossy codec, so exact round-trip is not expected.
    We verify that the SNR (signal-to-noise ratio) is acceptable for telephony.
    G.711 mu-law achieves ~35-38 dB SNR for voice signals.
    """

    def test_silence_round_trips_to_near_zero(self) -> None:
        """
        Test improvement B (tightened): tolerance changed from < 100 to < 8.

        G.711's smallest quantisation step is 2 * BIAS = 2 * 132 = 264 at exp=0,
        giving a decoded value of ±0. The exact silence encoded/decoded value is 0,
        so a tolerance of < 8 catches amplitude errors that the old < 100 missed.
        """
        original = np.zeros(160, dtype=np.int16)
        encoded = mulaw_encode(original)
        decoded = mulaw_decode(encoded)
        assert np.all(np.abs(decoded) < 8), (
            f"Silence round-trip: max abs = {np.max(np.abs(decoded))}, expected < 8"
        )

    def test_sine_wave_round_trip_snr_acceptable(self) -> None:
        """
        Test improvement B (tightened): added amplitude preservation check.

        The prior buggy codec amplitude-halved the signal (SNR still >20 dB but
        the decoded signal was only 50% of the input amplitude). The new check
        ensures the round-trip doesn't lose more than 5% of peak amplitude.
        """
        original = _sine_pcm_8k(440.0, n_samples=800)
        encoded = mulaw_encode(original)
        decoded = mulaw_decode(encoded)

        # SNR calculation: signal power / noise power
        signal_power = np.mean(original.astype(np.float64) ** 2)
        noise = decoded.astype(np.float64) - original.astype(np.float64)
        noise_power = np.mean(noise ** 2)

        if noise_power == 0:
            snr_db = float("inf")
        else:
            snr_db = 10 * np.log10(signal_power / noise_power)

        # G.711 mu-law achieves ~35–40 dB for voice. Minimum acceptable: 20 dB.
        assert snr_db > 20, f"Round-trip SNR too low: {snr_db:.1f} dB"

        # Amplitude preservation: decoded peak must be > 95% of original peak.
        # This catches amplitude-halving bugs that still pass SNR checks.
        max_original = float(np.max(np.abs(original.astype(np.float64))))
        max_decoded = float(np.max(np.abs(decoded.astype(np.float64))))
        assert max_decoded > 0.95 * max_original, (
            f"Amplitude halved: original peak={max_original:.0f}, "
            f"decoded peak={max_decoded:.0f} ({100 * max_decoded / max_original:.1f}%)"
        )


# ---------------------------------------------------------------------------
# Upsample 8kHz → 16kHz
# ---------------------------------------------------------------------------


class TestUpsample:
    def test_output_length_doubles(self) -> None:
        samples = _sine_pcm_8k(n_samples=160)
        upsampled = upsample_8k_to_16k(samples)
        assert len(upsampled) == 320

    def test_each_sample_is_repeated(self) -> None:
        samples = np.array([100, 200, 300], dtype=np.int16)
        upsampled = upsample_8k_to_16k(samples)
        expected = np.array([100, 100, 200, 200, 300, 300], dtype=np.int16)
        np.testing.assert_array_equal(upsampled, expected)

    def test_output_dtype_is_int16(self) -> None:
        samples = _sine_pcm_8k(n_samples=10)
        upsampled = upsample_8k_to_16k(samples)
        assert upsampled.dtype == np.int16

    def test_empty_input_gives_empty_output(self) -> None:
        samples = np.array([], dtype=np.int16)
        upsampled = upsample_8k_to_16k(samples)
        assert len(upsampled) == 0


# ---------------------------------------------------------------------------
# Downsample 16kHz → 8kHz
# ---------------------------------------------------------------------------


class TestDownsample:
    def test_output_length_halves(self) -> None:
        samples = np.arange(320, dtype=np.int16)
        downsampled = downsample_16k_to_8k(samples)
        assert len(downsampled) == 160

    def test_decimated_output_length_is_half_input(self) -> None:
        """
        Updated from test_even_indexed_samples_kept: The downsample now uses a
        21-tap FIR anti-aliasing filter before decimation (Bug 4 fix), so output
        samples are no longer the raw even-indexed input values. We test that the
        length and dtype are correct instead.
        """
        samples = np.array([10, 20, 30, 40, 50, 60], dtype=np.int16)
        downsampled = downsample_16k_to_8k(samples)
        # Length should be ceil(N/2) — FIR mode="same" preserves length, then [::2]
        assert len(downsampled) == len(samples) // 2
        assert downsampled.dtype == np.int16

    def test_output_dtype_is_int16(self) -> None:
        samples = np.zeros(100, dtype=np.int16)
        downsampled = downsample_16k_to_8k(samples)
        assert downsampled.dtype == np.int16

    def test_fir_attenuates_5khz_relative_to_1khz(self) -> None:
        """
        Bug A regression test: FIR cutoff must be 3600 Hz, not 7200 Hz.

        With the broken cutoff_norm=0.45 (fc=7200 Hz), a 5 kHz sine wave passes
        through with almost no attenuation, so when it aliases at 8 kHz it appears
        at 3 kHz in the output — contaminating the voice band.

        With the correct cutoff_norm=0.225 (fc=3600 Hz), the 5 kHz tone is
        attenuated by at least 20 dB relative to a 1 kHz reference tone.

        Method: generate long signals (1 second = 16000 samples at 16 kHz),
        pass each through the FIR (downsample to 8 kHz), measure output energy
        via FFT.
        """
        fs_in = 16_000
        n_samples = 16_000  # 1 second at 16 kHz
        t = np.arange(n_samples) / fs_in

        # Reference: 1 kHz sine (well within pass-band)
        ref_1k = np.sin(2.0 * np.pi * 1000.0 * t)
        pcm_1k = (ref_1k * 16383.0).astype(np.int16)
        out_1k = downsample_16k_to_8k(pcm_1k).astype(np.float64)

        # Test: 5 kHz sine (above 3.6 kHz cutoff, below old 7.2 kHz cutoff)
        ref_5k = np.sin(2.0 * np.pi * 5000.0 * t)
        pcm_5k = (ref_5k * 16383.0).astype(np.int16)
        out_5k = downsample_16k_to_8k(pcm_5k).astype(np.float64)

        energy_1k = np.mean(out_1k ** 2)
        energy_5k = np.mean(out_5k ** 2)

        assert energy_1k > 0, "1 kHz reference tone has zero energy — check FIR"
        assert energy_5k >= 0, "5 kHz energy must be non-negative"

        # Attenuation must be at least 20 dB (factor of 100 in power).
        # If the old cutoff (7200 Hz) is used, 5 kHz passes with ~0 dB attenuation.
        if energy_5k == 0:
            attenuation_db = float("inf")
        else:
            attenuation_db = 10.0 * np.log10(energy_1k / energy_5k)

        assert attenuation_db >= 20.0, (
            f"5 kHz tone not sufficiently attenuated: attenuation={attenuation_db:.1f} dB "
            f"(need >= 20 dB). FIR cutoff is likely wrong (Bug A: cutoff_norm should be "
            f"0.225 for fc=3600 Hz, not 0.45 for fc=7200 Hz)."
        )


# ---------------------------------------------------------------------------
# Combined convenience functions
# ---------------------------------------------------------------------------


class TestMulawToPcm16k:
    def test_output_length_is_4x_input(self) -> None:
        # Input: N mu-law bytes at 8kHz.
        # Decode: N int16 samples at 8kHz.
        # Upsample: 2N int16 samples at 16kHz.
        # tobytes(): 2N * 2 = 4N bytes.
        n = 160
        mulaw = _silence_mulaw(n)
        pcm = mulaw_to_pcm16k(mulaw)
        assert len(pcm) == n * 4

    def test_output_is_bytes(self) -> None:
        result = mulaw_to_pcm16k(_silence_mulaw(80))
        assert isinstance(result, bytes)

    def test_silence_decodes_to_near_zero_pcm(self) -> None:
        pcm = mulaw_to_pcm16k(_silence_mulaw(160))
        samples = np.frombuffer(pcm, dtype=np.int16)
        assert np.all(np.abs(samples) < 100)


class TestPcm16kToMulaw:
    def test_output_length_is_input_samples_divided_by_two(self) -> None:
        # Input: 2N int16 samples at 16kHz as bytes → 4N bytes.
        # Downsample: N int16 samples at 8kHz.
        # Encode: N mu-law bytes.
        n_samples_16k = 320  # 20ms at 16kHz
        pcm_bytes = np.zeros(n_samples_16k, dtype=np.int16).tobytes()
        mulaw_bytes = pcm16k_to_mulaw(pcm_bytes)
        assert len(mulaw_bytes) == n_samples_16k // 2

    def test_output_is_bytes(self) -> None:
        pcm = np.zeros(100, dtype=np.int16).tobytes()
        result = pcm16k_to_mulaw(pcm)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# End-to-end: mulaw → pcm16k → mulaw (full bridge round trip)
# ---------------------------------------------------------------------------


class TestBridgeRoundTrip:
    """
    Simulate the full bridge: Twilio → LiveKit → Twilio.
    mulaw_to_pcm16k() → pcm16k_to_mulaw() should reconstruct the original
    mu-law bytes within G.711 lossy codec tolerance.
    """

    def test_silence_round_trips(self) -> None:
        original_mulaw = _silence_mulaw(160)
        pcm_16k = mulaw_to_pcm16k(original_mulaw)
        reconstructed_mulaw = pcm16k_to_mulaw(pcm_16k)

        # Silence round-trip: all bytes should be the same silence value.
        unique_original = set(original_mulaw)
        unique_reconstructed = set(reconstructed_mulaw)
        assert unique_reconstructed == unique_original, (
            f"Silence round-trip mismatch: {unique_reconstructed} vs {unique_original}"
        )

    def test_sine_wave_round_trip_is_stable(self) -> None:
        """
        Sine wave at 8kHz → pcm16k → mulaw → decode and compare to original decoded PCM.
        The round-trip introduces quantization noise but should stay within G.711 tolerance.
        """
        original_mulaw = mulaw_encode(_sine_pcm_8k(440.0, n_samples=160))
        pcm_16k = mulaw_to_pcm16k(original_mulaw)
        reconstructed_mulaw = pcm16k_to_mulaw(pcm_16k)

        # Decode both and compare SNR.
        original_pcm = mulaw_decode(original_mulaw).astype(np.float64)
        reconstructed_pcm = mulaw_decode(reconstructed_mulaw).astype(np.float64)

        signal_power = np.mean(original_pcm ** 2)
        noise_power = np.mean((original_pcm - reconstructed_pcm) ** 2)

        if noise_power == 0:
            return  # Perfect round-trip

        snr_db = 10 * np.log10(signal_power / noise_power)
        assert snr_db > 20, f"Bridge round-trip SNR too low: {snr_db:.1f} dB"

    def test_20ms_twilio_packet_size(self) -> None:
        """
        Twilio sends 160-sample mu-law packets (20ms at 8kHz).
        The bridge should handle exactly this size without errors.
        """
        # 160 mu-law bytes → 640 bytes of 16kHz PCM → 160 mu-law bytes.
        packet = _silence_mulaw(160)
        pcm = mulaw_to_pcm16k(packet)
        assert len(pcm) == 640  # 320 samples * 2 bytes/sample
        reconstructed = pcm16k_to_mulaw(pcm)
        assert len(reconstructed) == 160
