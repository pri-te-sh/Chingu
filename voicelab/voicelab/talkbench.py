"""Drive /ws/talk with typed turns to measure whole-turn latency per stack (no mic needed).
   uv run python -m voicelab.talkbench kokoro af_heart 4"""
import asyncio, json, sys, time
import websockets

PROMPTS = ["Hey Pixel, I'm leaving for work now.", "What should I cook tonight with chicken and spinach?", "I skipped the gym again today.",
           "Tell me a fun fact in one sentence.", "How are you feeling today?", "Good night, see you tomorrow."]

async def main():
    tts = sys.argv[1] if len(sys.argv) > 1 else "kokoro"; voice = sys.argv[2] if len(sys.argv) > 2 else None; n = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    async with websockets.connect("ws://localhost:8790/ws/talk", max_size=None) as ws:
        await ws.send(json.dumps({"stt": "parakeet-0.6b", "tts": tts, "voice": voice, "vad": "energy"}))
        for p in PROMPTS[:n]:
            await ws.send(json.dumps({"type": "text", "text": p})); t0 = time.perf_counter(); audio = 0; reply = ""
            while True:
                m = await ws.recv()
                if isinstance(m, bytes): audio += len(m); continue
                d = json.loads(m)
                if d["type"] == "speech_end":
                    print(f"{tts}/{voice}: first token {d.get('t_first_token')} ms  first audio {d.get('t_first_audio')} ms  done {d.get('t_done')} ms  audio {audio/32000:.1f}s   | {p[:32]} -> {d['reply'][:70]}"); break
                if d["type"] == "error": print("error", d["message"]); break

asyncio.run(main())
