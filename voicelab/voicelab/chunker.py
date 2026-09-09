"""Turns an LLM token stream into speakable chunks: the first clause goes out as early as possible, then sentences.
Scans every boundary in the buffer (a too-short clause never blocks a later sentence end) and caps chunk length."""
import re
_END = re.compile(r"([.!?]+[\"')\]]?\s+|[,;:]\s+(?=\S))")

class Chunker:
    def __init__(self, first_min=12, min_chars=40, max_chars=160):
        self.buf = ""; self.first = True; self.first_min = first_min; self.min_chars = min_chars; self.max_chars = max_chars
    def _take(self, cut):
        cand = self.buf[:cut].strip(); self.buf = self.buf[cut:]; self.first = False; return cand
    def feed(self, delta: str):
        self.buf += delta; out = []
        while True:
            need = self.first_min if self.first else self.min_chars
            cut = None; last_ok = None
            for m in _END.finditer(self.buf):
                cand = self.buf[:m.end()].strip()
                strong = cand[-1:] in ".!?" or cand[-2:-1] in ".!?"
                if strong or len(cand) >= need: cut = m.end(); break
                if len(cand) <= self.max_chars: last_ok = m.end()
            if cut is None and len(self.buf) > self.max_chars and last_ok:   # long run without a sentence end: cut at the last clause
                cut = last_ok
            if cut is None: break
            c = self._take(cut)
            if c: out.append(c)
        return out
    def flush(self):
        t = self.buf.strip(); self.buf = ""; self.first = True; return [t] if t else []
