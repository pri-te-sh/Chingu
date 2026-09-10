"""STT engine selection: default from config, per-call override, whisper fallback when Parakeet is unavailable."""
import pixel.stt as stt

def test_override_falls_back_to_whisper_when_parakeet_fails(monkeypatch):
    monkeypatch.setattr(stt, "transcribe_whisper", lambda pcm: "whisper text")
    def boom(pcm): raise RuntimeError("no model")
    monkeypatch.setattr(stt, "transcribe_parakeet", boom)
    assert stt.transcribe(b"\x00" * 3200, "parakeet") == "whisper text"

def test_default_engine_used_when_no_override(monkeypatch):
    monkeypatch.setattr(stt, "_engine", "parakeet")
    monkeypatch.setattr(stt, "transcribe_parakeet", lambda pcm: "parakeet text")
    monkeypatch.setattr(stt, "transcribe_whisper", lambda pcm: "whisper text")
    assert stt.transcribe(b"\x00" * 3200) == "parakeet text"
    assert stt.transcribe(b"\x00" * 3200, "whisper") == "whisper text"
