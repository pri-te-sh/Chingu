from pixel import llm, tools
from pixel.vad import EnergyVAD
import numpy as np


def test_tag_parsing():
    assert llm.parse_tag("[happy 0.8] Hi there") == ("happy", 0.8, "Hi there")
    assert llm.parse_tag("[bogus 0.5] x")[0] == "neutral"
    assert llm.parse_tag("[hap") is None                     # incomplete tag: wait
    assert llm.parse_tag("Plain text")[2] == "Plain text"    # no tag: neutral


def test_vad_detects_utterance():
    vad = EnergyVAD(start_rms=900, end_rms=500, min_speech_ms=300, end_silence_ms=700)
    silence = (np.zeros(320, dtype=np.int16)).tobytes()
    loud = (np.random.randint(-6000, 6000, 320, dtype=np.int16)).tobytes()
    for _ in range(5): assert vad.feed(silence) is None
    for _ in range(30): assert vad.feed(loud) is None       # 600 ms of speech
    got = None
    for _ in range(50):                                      # ~1 s silence -> end of utterance
        got = vad.feed(silence) or got
    assert got and len(got) > 30 * 640
    # the default pause is longer (people pause mid-sentence): 1 s of silence must NOT end the turn, 1.5 s must
    vad = EnergyVAD(start_rms=900, end_rms=500, min_speech_ms=300)
    for _ in range(30): vad.feed(loud)
    assert all(vad.feed(silence) is None for _ in range(50))
    assert any(vad.feed(silence) for _ in range(30))


def test_narration_templates():
    text, expr = tools.narrate_before([{"function": {"name": "web_search", "arguments": {"query": "current weather in Toronto"}}}])
    assert text and expr == "curious"
    assert tools.narrate_after([("web_search", "{}", "No results.")])[1] == "sad"
    assert tools.narrate_after([("remember", "{}", "Saved")]) is None
