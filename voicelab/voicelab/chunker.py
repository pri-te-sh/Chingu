"""Turns an LLM token stream into speakable chunks: the first clause goes out as early as possible, then sentences."""
import re
_END = re.compile(r"([.!?]+[\"')\]]?\s+|[,;:]\s+(?=\S))")

class Chunker:
    def __init__(self, first_min=12, min_chars=40):
        self.buf = ""; self.first = True; self.first_min = first_min; self.min_chars = min_chars
    def feed(self, delta: str):
        self.buf += delta; out = []
        while True:
            m = _END.search(self.buf)
            if not m: break
            cut = m.end(); cand = self.buf[:cut].strip()
            strong = cand[-1:] in ".!?" or cand[-2:-1] in ".!?"
            need = self.first_min if self.first else self.min_chars
            if strong or len(cand) >= need:
                out.append(cand); self.buf = self.buf[cut:]; self.first = False
            else: break
        return out
    def flush(self):
        t = self.buf.strip(); self.buf = ""; return [t] if t else []
