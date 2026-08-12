"""Audio resampling utilities for the WebRTC bridge.

Converts between LiveKit room audio (48kHz) and provider WebSocket audio
(typically 16kHz for Vapi/Retell).
"""

try:
    import audioop

    _HAS_AUDIOOP = True
except ImportError:
    _HAS_AUDIOOP = False


def resample_pcm(
    data: bytes,
    from_rate: int,
    to_rate: int,
    channels: int = 1,
) -> bytes:
    """Resample 16-bit PCM audio between sample rates.

    Uses ``audioop.ratecv`` (stdlib) for linear interpolation.
    Sufficient for voice audio.  Swap to ``soxr`` if higher quality is needed.

    On Python 3.13+ where ``audioop`` was removed, returns the data
    unchanged and logs a warning on first call.
    """
    if from_rate == to_rate:
        return data
    if not _HAS_AUDIOOP:
        raise RuntimeError(
            "audioop is not available (removed in Python 3.13). "
            "Install the 'audioop-lts' package or use Python < 3.13."
        )
    converted, _ = audioop.ratecv(data, 2, channels, from_rate, to_rate, None)
    return converted
