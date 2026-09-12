"""Uplink audio codecs. Devices with a thin uplink (the ESP32 gets ~170 kbit/s to the brain) send G.711 mu-law
instead of raw PCM16: 8 bits/sample at 16 kHz = 128 kbit/s. The device announces it in hello.capabilities.audio_in."""
import numpy as np

_TABLE = np.zeros(256, dtype=np.int16)
for _u in range(256):
    _v = ~_u & 0xFF
    _sign, _exp, _mant = _v & 0x80, (_v >> 4) & 0x07, _v & 0x0F
    _mag = ((_mant << 3) + 0x84) << _exp
    _mag -= 0x84
    _TABLE[_u] = -_mag if _sign else _mag


def decode(fmt: str | None, data: bytes) -> bytes:
    """Bytes from the device -> PCM16 little-endian mono at the pipeline rate."""
    if not fmt or fmt == "pcm16":
        return data
    if fmt == "mulaw":
        return _TABLE[np.frombuffer(data, dtype=np.uint8)].tobytes()
    raise ValueError(f"unknown audio_in format {fmt!r}")


def encode_mulaw(pcm: bytes) -> bytes:
    """Reference encoder (tests / simulator)."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
    sign = np.where(x < 0, 0x80, 0); x = np.minimum(np.abs(x), 32635) + 0x84
    exp = np.floor(np.log2(x)).astype(np.int32) - 7
    mant = (x >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mant) & 0xFF).astype(np.uint8).tobytes()
