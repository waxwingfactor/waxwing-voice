"""
Audio codec utilities for the Twilio ↔ LiveKit bridge.

Provides:
  - Pure-Python mu-law (G.711 PCMU) decode table: 256 → 16-bit PCM samples.
  - Pure-Python mu-law encode function: 16-bit PCM → 8-bit mu-law byte.
  - numpy-based resampler: 8 kHz ↔ 16 kHz (integer 1:2 ratio).
  - Anti-aliased 16→8 kHz downsample via 21-tap FIR lowpass filter (3.6 kHz cutoff).

No audioop. audioop was removed from the Python standard library in Python 3.13.
All codec work here uses only numpy, which is a first-class dependency of the
bridge package.

Design notes
------------
Mu-law decode table: precomputed at import time using the ITU-T G.711 / Sun/SOX
reference algorithm. 256 entries (one per possible byte value), each a 16-bit
signed PCM sample. Reference values: MULAW_DECODE_TABLE[0x00] = -32124,
MULAW_DECODE_TABLE[0xFF] = 0, MULAW_DECODE_TABLE[0x80] = 32124.

Mu-law encode table: 65536-entry lookup table (one per int16 value + 32768 offset)
precomputed from the same ITU-T G.711 reference. Reference: encode(0) = 0xFF,
encode(32767) = 0x80, encode(-32767) = 0x00.

Resampling (8→16 kHz): nearest-neighbor repeat. No filter needed on upsample since
we are adding samples, not removing them.

Resampling (16→8 kHz): a 21-tap FIR lowpass filter (cutoff 3.6 kHz) is applied
before decimation-by-2 to prevent aliasing of TTS sibilance (above 4 kHz) into the
voice band. The FIR coefficients are precomputed at import time using the
scipy-free windowed-sinc method (numpy only).

Thread-safety: All precomputed tables are read-only after import. Codec functions
are pure (no shared state). Safe to call from multiple coroutines.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Mu-law decode table  (Bug 1 fix: ITU-T G.711 / Sun/SOX reference formula)
# ---------------------------------------------------------------------------
# G.711 PCMU decode: 8-bit mu-law byte → 16-bit signed PCM sample.
# Reference values: TABLE[0x00]=-32124, TABLE[0xFF]=0, TABLE[0x80]=32124.
# Precomputed at import time (O(1) lookup per sample at runtime).

def _build_mulaw_decode_table() -> list[int]:
    """
    Build the 256-entry ITU-T G.711 mu-law → int16 PCM decode table.

    Uses the Sun/SOX reference algorithm. Each mu-law byte is first bit-inverted
    (G.711 encodes with all bits complemented), then the sign, exponent, and
    mantissa fields are extracted and used to reconstruct the linear value.

    Reference: Sun Microsystems / SOX ulaw2linear.
    """
    table: list[int] = []
    for byte in range(256):
        # G.711: all bits are complemented before transmission.
        inverted = (~byte) & 0xFF
        sign = inverted & 0x80
        exp = (inverted >> 4) & 0x07
        mant = inverted & 0x0F
        # Reconstruct: add bias 0x84 (132), shift by exponent, remove bias.
        linear = ((mant << 3) + 0x84) << exp
        linear -= 0x84
        if sign:
            linear = -linear
        table.append(linear)
    return table


# Module-level constant — never modified after import.
MULAW_DECODE_TABLE: list[int] = _build_mulaw_decode_table()

# Precomputed as numpy array for fast vectorised decode.
_MULAW_DECODE_NP = np.array(MULAW_DECODE_TABLE, dtype=np.int16)


# ---------------------------------------------------------------------------
# Mu-law encode  (Bug 2 fix: sign bit was inverted; exponent used wrong mask)
# ---------------------------------------------------------------------------

def _encode_one_sample(sample: int) -> int:
    """
    Encode a single 16-bit PCM signed sample to an 8-bit mu-law byte.

    Uses the ITU-T G.711 / Sun/SOX reference algorithm.
    Reference: encode(0)=0xFF, encode(32767)=0x80, encode(-32767)=0x00.

    Args:
        sample: Signed 16-bit PCM sample in range [-32768, 32767].

    Returns:
        Unsigned 8-bit mu-law byte value in range [0, 255].
    """
    BIAS = 0x84   # 132 — G.711 bias added before log quantisation
    CLIP = 32635  # max magnitude (leave headroom for bias addition)

    # Sign: 0x80 for negative samples, 0x00 for non-negative.
    if sample < 0:
        sign = 0x80
        sample = -sample
    else:
        sign = 0x00

    # Clip magnitude.
    if sample > CLIP:
        sample = CLIP

    # Add bias.
    sample += BIAS  # now in range [132, 32767]

    # Exponent: position of the highest set bit, offset so bit-7 → exp=0.
    # sample is in [132, 32767], so bit_length is in [8, 15].
    # exp = bit_length - 8  maps [8..15] → [0..7].
    exp = max(0, min(7, sample.bit_length() - 8))

    # Mantissa: the 4 bits below the leading 1.
    mant = (sample >> (exp + 3)) & 0x0F

    # G.711 complements all bits before transmission.
    return (~(sign | (exp << 4) | mant)) & 0xFF


# Precompute a 65536-entry encode table for O(1) per-sample encoding.
# Index = uint16 view of int16 (i.e. int16 + 32768).
def _build_encode_table() -> np.ndarray:
    table = np.zeros(65536, dtype=np.uint8)
    for i in range(65536):
        table[i] = _encode_one_sample(i - 32768)
    return table


_MULAW_ENCODE_TABLE = _build_encode_table()


# ---------------------------------------------------------------------------
# Anti-aliased 16→8 kHz downsample FIR filter  (Bug 4 fix; Bug A fix)
# ---------------------------------------------------------------------------
# A 21-tap windowed-sinc lowpass filter with cutoff at 3.6 kHz (< Nyquist/2
# of 4 kHz for 8 kHz output) prevents TTS sibilance above 4 kHz from aliasing
# into the voice band when we decimate by 2.
#
# Coefficients computed with the windowed-sinc method (numpy only, no scipy).
#
# Bug A fix: The prior cutoff_norm was 0.45, which in the convention
#   fc = cutoff_norm × Fs gives fc = 0.45 × 16000 = 7200 Hz — well above
#   the 4 kHz Nyquist of the 8 kHz output, so the "anti-aliasing" filter did
#   not anti-alias at all. The correct value is:
#   fc = 3600 Hz → cutoff_norm = 3600 / 16000 = 0.225.
# The L'Hôpital limit at offset=0 is 2*cutoff_norm, updated to 0.45 accordingly.
#
# Cutoff = 3600 Hz, sample rate = 16000 Hz → normalised cutoff = 0.225.

def _build_fir_lowpass(num_taps: int = 21, cutoff_norm: float = 0.225) -> np.ndarray:
    """
    Build a windowed-sinc FIR lowpass filter kernel.

    Args:
        num_taps:     Number of filter taps (must be odd for symmetric FIR).
        cutoff_norm:  Normalised cutoff frequency (0.0–1.0, where 1.0 = Nyquist).
                      Default 0.225 → fc = 0.225 × 16000 = 3600 Hz cutoff
                      (below the 4 kHz Nyquist of the 8 kHz output).

    Returns:
        numpy float64 array of length num_taps, sum ≈ 1.0.
    """
    n = np.arange(num_taps, dtype=np.float64)
    centre = (num_taps - 1) / 2.0
    offset = n - centre  # zero at the centre tap
    # Sinc kernel — handle the DC tap (offset==0) explicitly to avoid 0/0.
    with np.errstate(invalid="ignore", divide="ignore"):
        sinc_vals = np.sin(2.0 * np.pi * cutoff_norm * offset) / (np.pi * offset)
    # L'Hôpital limit: lim_{x→0} sin(2π·fc·x)/(π·x) = 2·fc = 2·cutoff_norm
    sinc_vals[offset == 0.0] = 2.0 * cutoff_norm  # = 0.45 for cutoff_norm=0.225
    # Hann window
    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / (num_taps - 1))
    h = sinc_vals * window
    return (h / h.sum()).astype(np.float64)  # normalise to unit gain


_FIR_COEFFS = _build_fir_lowpass(num_taps=21, cutoff_norm=0.225)


# ---------------------------------------------------------------------------
# Public codec functions
# ---------------------------------------------------------------------------


def mulaw_decode(mulaw_bytes: bytes | bytearray) -> np.ndarray:
    """
    Decode a buffer of mu-law bytes to 16-bit signed PCM samples.

    Args:
        mulaw_bytes: Raw PCMU bytes from Twilio Media Streams.
                     Each byte is one sample at 8 kHz.

    Returns:
        numpy array of dtype int16, shape (N,) where N = len(mulaw_bytes).
        Sample rate: 8 kHz.
    """
    indices = np.frombuffer(mulaw_bytes, dtype=np.uint8)
    return _MULAW_DECODE_NP[indices]


def mulaw_encode(pcm_samples: np.ndarray) -> bytes:
    """
    Encode 16-bit signed PCM samples to mu-law bytes (ITU-T G.711).

    Uses a precomputed 65536-entry lookup table for O(1) per-sample encoding.
    Reference: encode(np.int16(0))=0xFF, encode(np.int16(32767))=0x80.

    Args:
        pcm_samples: numpy array of dtype int16, shape (N,). Sample rate: 8 kHz.

    Returns:
        bytes of length N, one mu-law byte per sample.
    """
    # Shift signed int16 to unsigned index [0, 65535] — int16(0) → index 32768.
    indices = pcm_samples.astype(np.int32) + 32768
    indices = np.clip(indices, 0, 65535).astype(np.uint32)
    return _MULAW_ENCODE_TABLE[indices].tobytes()


def upsample_8k_to_16k(pcm_8k: np.ndarray) -> np.ndarray:
    """
    Upsample 8 kHz PCM to 16 kHz by repeating each sample (nearest-neighbor).

    The 8 kHz → 16 kHz ratio is exactly 2:1, so no anti-aliasing filter is
    needed for voice-quality telephony audio. Each 8 kHz sample becomes two
    identical 16 kHz samples.

    Args:
        pcm_8k: numpy array of dtype int16, shape (N,). Sample rate: 8 kHz.

    Returns:
        numpy array of dtype int16, shape (2N,). Sample rate: 16 kHz.
    """
    # np.repeat([a, b, c], 2) → [a, a, b, b, c, c]
    return np.repeat(pcm_8k, 2)


def downsample_16k_to_8k(pcm_16k: np.ndarray) -> np.ndarray:
    """
    Downsample 16 kHz PCM to 8 kHz with anti-aliased decimation (Bug 4 fix).

    Applies a 21-tap FIR lowpass filter (cutoff ~3.6 kHz, < Nyquist of 4 kHz)
    before decimation-by-2. This prevents TTS sibilance above 4 kHz from aliasing
    into the voice band, which was audible with the prior naive [::2] decimation.

    The filter uses numpy convolution only — no scipy required.

    Args:
        pcm_16k: numpy array of dtype int16, shape (2N,). Sample rate: 16 kHz.

    Returns:
        numpy array of dtype int16, shape (N,). Sample rate: 8 kHz.
    """
    n = len(pcm_16k)
    if n == 0:
        return pcm_16k
    # Convolve with FIR coefficients in float64.
    # np.convolve mode="same" returns max(M, N) samples when the kernel is larger
    # than the signal — always slice to exactly n samples to keep consistent length.
    filtered_full = np.convolve(pcm_16k.astype(np.float64), _FIR_COEFFS, mode="same")
    filtered = filtered_full[:n]  # guard against kernel-larger-than-signal edge case
    # Decimate by 2 and convert back to int16.
    return np.clip(filtered[::2], -32768, 32767).astype(np.int16)


def mulaw_to_pcm16k(mulaw_bytes: bytes | bytearray) -> bytes:
    """
    Convert a buffer of Twilio mu-law bytes (8 kHz) to 16 kHz PCM bytes.

    Combined decode + upsample, output ready for LiveKit AudioFrame.

    Args:
        mulaw_bytes: PCMU buffer from Twilio Media Streams.

    Returns:
        16-bit signed little-endian PCM bytes at 16 kHz mono.
    """
    pcm_8k = mulaw_decode(mulaw_bytes)
    pcm_16k = upsample_8k_to_16k(pcm_8k)
    # tobytes() gives little-endian int16 on all platforms when dtype=int16
    return pcm_16k.tobytes()


def pcm16k_to_mulaw(pcm_bytes: bytes | bytearray) -> bytes:
    """
    Convert 16 kHz PCM bytes (from LiveKit) to mu-law bytes for Twilio.

    Combined downsample + encode.

    Args:
        pcm_bytes: 16-bit signed little-endian PCM at 16 kHz mono.

    Returns:
        PCMU bytes at 8 kHz, one byte per sample.
    """
    pcm_16k = np.frombuffer(pcm_bytes, dtype=np.int16)
    pcm_8k = downsample_16k_to_8k(pcm_16k)
    return mulaw_encode(pcm_8k)


# ---------------------------------------------------------------------------
# Struct helpers for raw PCM bytes
# ---------------------------------------------------------------------------

def pcm_bytes_to_array(pcm_bytes: bytes | bytearray) -> np.ndarray:
    """Convert raw 16-bit little-endian PCM bytes to a numpy int16 array."""
    return np.frombuffer(pcm_bytes, dtype=np.int16)


def array_to_pcm_bytes(samples: np.ndarray) -> bytes:
    """Convert a numpy int16 array to raw 16-bit little-endian PCM bytes."""
    return samples.astype(np.int16).tobytes()
