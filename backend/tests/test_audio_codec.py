import numpy as np
from pixel import audio_codec


def test_mulaw_roundtrip_is_close():
    t = np.arange(1600) / 16000.0
    pcm = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    out = np.frombuffer(audio_codec.decode("mulaw", audio_codec.encode_mulaw(pcm.tobytes())), dtype=np.int16)
    assert out.shape == pcm.shape
    err = np.abs(out.astype(int) - pcm.astype(int))
    assert err.max() < 600 and err.mean() < 120          # 8-bit companding: fine for speech, ~1-2% error


def test_pcm_passthrough_and_unknown():
    assert audio_codec.decode(None, b"\x01\x02") == b"\x01\x02"
    assert audio_codec.decode("pcm16", b"\x01\x02") == b"\x01\x02"
    try: audio_codec.decode("opus", b""); assert False
    except ValueError: pass
