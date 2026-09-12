"""Tiny energy-based voice activity detector operating on PCM16 chunks. Deliberately simple so it behaves
identically for the laptop client and the ESP32; upgrade to Silero later if false triggers become a problem."""
import numpy as np
from . import config as C


class EnergyVAD:
    def __init__(self, start_rms: int | None = None, end_rms: int | None = None, min_speech_ms: int | None = None, end_silence_ms: int | None = None):
        self.end_silence_ms = end_silence_ms or C.VAD_END_SILENCE_MS   # how long a pause ends the utterance; people pause mid-sentence, so err long
        self.start_rms = start_rms or C.VAD_START_RMS
        self.end_rms = end_rms or C.VAD_END_RMS
        self.min_speech_ms = min_speech_ms or C.VAD_MIN_SPEECH_MS
        self.reset()

    def reset(self):
        self.speaking = False
        self.buffer = bytearray()
        self.silence_ms = 0.0
        self.speech_ms = 0.0

    def feed(self, pcm: bytes) -> bytes | None:
        """Feed a chunk. Returns the finished utterance (PCM16 bytes) when end-of-speech is detected, else None."""
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return None
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        dur_ms = samples.size / C.SAMPLE_RATE * 1000

        if not self.speaking:
            self.buffer += pcm                       # keep a little pre-roll so we don't clip the first syllable
            if len(self.buffer) > C.SAMPLE_RATE * 2 * 0.4:
                del self.buffer[: len(self.buffer) - int(C.SAMPLE_RATE * 2 * 0.4)]
            if rms > self.start_rms:
                self.speaking = True
                self.speech_ms = dur_ms
                self.silence_ms = 0
            return None

        self.buffer += pcm
        self.speech_ms += dur_ms
        if rms < self.end_rms:
            self.silence_ms += dur_ms
        else:
            self.silence_ms = 0

        if (self.silence_ms >= self.end_silence_ms and self.speech_ms >= self.min_speech_ms) \
                or self.speech_ms > C.VAD_MAX_UTTERANCE_S * 1000:
            utt = bytes(self.buffer)
            self.reset()
            return utt
        return None

    def flush(self) -> bytes | None:
        """Client says it is done talking (e.g. button release): return whatever we have."""
        utt = bytes(self.buffer) if self.speaking and self.speech_ms >= self.min_speech_ms else None
        self.reset()
        return utt
