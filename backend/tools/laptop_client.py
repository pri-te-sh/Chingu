#!/usr/bin/env python3
"""Stand-in for the ESP32: streams the laptop mic to the server, plays the reply through the speakers,
prints every protocol event. Usage:
  python tools/laptop_client.py                       # talk (energy VAD ends your utterance)
  python tools/laptop_client.py --text "hello there"  # typed input, no mic needed
  python tools/laptop_client.py --url wss://...modal.run/ws --token SECRET
"""
import argparse, asyncio, json, os, sys, time
import numpy as np
import websockets

SR = 16000
CHUNK = 320  # 20 ms


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8765/ws")
    ap.add_argument("--token", default=os.environ.get("PIXEL_TOKEN", ""))
    ap.add_argument("--text", help="send typed text instead of using the microphone")
    ap.add_argument("--turns", type=int, default=1, help="how many utterances to run in mic mode")
    args = ap.parse_args()

    async with websockets.connect(args.url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "hello", "token": args.token, "device": "laptop"}))
        print("<-", await ws.recv())

        player = None
        if not args.text or True:
            try:
                import sounddevice as sd
                player = sd.RawOutputStream(samplerate=SR, channels=1, dtype="int16"); player.start()
            except Exception as e:
                print(f"(no audio output: {e})")

        async def receiver(stop_after_reply: asyncio.Event):
            t_start = None
            async for msg in ws:
                if isinstance(msg, bytes):
                    if player: player.write(msg)
                    continue
                data = json.loads(msg)
                if data["type"] == "speech_start": t_start = time.time()
                if data["type"] == "speech_end" and t_start: print(f"   (speech streamed for {time.time() - t_start:.1f}s)")
                print("<-", msg[:160])
                if data["type"] == "reply":
                    stop_after_reply.set()

        if args.text:
            done = asyncio.Event()
            recv = asyncio.create_task(receiver(done))
            await ws.send(json.dumps({"type": "text", "text": args.text}))
            await done.wait(); await asyncio.sleep(0.5); recv.cancel(); return

        import sounddevice as sd
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        def cb(indata, frames, t, status): loop.call_soon_threadsafe(q.put_nowait, bytes(indata))
        for _ in range(args.turns):
            done = asyncio.Event()
            recv = asyncio.create_task(receiver(done))
            print("\n>> speak now...")
            with sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK, callback=cb):
                while not done.is_set():
                    try:
                        await ws.send(await asyncio.wait_for(q.get(), timeout=0.1))
                    except asyncio.TimeoutError:
                        pass
            await asyncio.sleep(0.3); recv.cancel()


asyncio.run(main())
