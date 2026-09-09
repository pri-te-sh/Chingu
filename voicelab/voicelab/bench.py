"""Smoke/bench CLI: synthesise the phrase set with every TTS, transcribe it with every STT, print a table.
   uv run python -m voicelab.bench            (first run downloads models: whisper-turbo ~1.6 GB, parakeet ~0.7 GB, kokoro ~0.3 GB)"""
import sys, time, difflib
from .common import ROOT, Timer, record, SR
from .engines import stt as S, tts as T

def wer(ref: str, hyp: str) -> float:
    r = [w.strip(".,!?'\"").lower() for w in ref.split()]; h = [w.strip(".,!?'\"").lower() for w in hyp.split()]
    sm = difflib.SequenceMatcher(a=r, b=h); errs = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    return errs / max(1, len(r))

def main():
    phrases = [l.strip() for l in (ROOT / "phrases.txt").read_text().splitlines() if l.strip()]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    phrases = phrases[:n]
    tts_names = [x for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else ["piper", "kokoro"]
    stt_names = [x for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else list(S.ENGINES)

    print("== TTS")
    audio = {}
    for name in tts_names:
        e = T.ENGINES[name]
        try:
            with Timer() as t: e.load()
            print(f"{name:10s} loaded in {t.ms:.0f} ms")
        except Exception as ex: print(f"{name:10s} unavailable: {ex}"); continue
        for ph in phrases:
            t0 = time.perf_counter(); first = None; chunks = []
            for c in e.stream(ph):
                if first is None: first = (time.perf_counter() - t0) * 1000
                chunks.append(c)
            total = (time.perf_counter() - t0) * 1000; pcm = b"".join(chunks); dur = len(pcm) / 2 / SR
            audio.setdefault(name, []).append(pcm)
            record("tts", engine=name, voice=e.voices[0], text=ph, ttfa_ms=first, total_ms=total, audio_s=dur, rtf=total / 1000 / max(dur, .01))
            print(f"  {name:8s} ttfa {first:5.0f} ms  total {total:5.0f} ms  audio {dur:4.1f}s  rtf {total/1000/max(dur,.01):.2f}   {ph[:50]}")

    print("\n== STT (input = kokoro audio if available, else piper)")
    src = "kokoro" if "kokoro" in audio else next(iter(audio), None)
    if not src: print("no TTS audio to transcribe"); return
    for name in stt_names:
        e = S.ENGINES[name]
        try:
            with Timer() as t: e.load()
            print(f"{name:12s} loaded in {t.ms:.0f} ms")
        except Exception as ex: print(f"{name:12s} unavailable: {ex}"); continue
        tot_w = 0; tot_ms = 0
        for ph, pcm in zip(phrases, audio[src]):
            with Timer() as t: hyp = e.transcribe(pcm)
            w = wer(ph, hyp); tot_w += w; tot_ms += t.ms
            record("stt", engine=name, ref=ph, hyp=hyp, ms=t.ms, wer=w, source=src)
            print(f"  {name:10s} {t.ms:5.0f} ms  wer {w:4.2f}   {hyp[:60]}")
        print(f"  -> {name}: mean {tot_ms/len(phrases):.0f} ms, mean WER {tot_w/len(phrases):.2f}")

if __name__ == "__main__": main()
