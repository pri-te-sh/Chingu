#!/usr/bin/env python3
"""Drive every expression over serial and echo the board's replies/stats. Usage: .venv/bin/python tools/expr_tour.py [dwell_s]"""
import sys, time, serial
PORT, BAUD = "/dev/cu.usbserial-110", 115200
dwell = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
exprs = "neutral happy excited curious thinking listening surprised suspicious annoyed sad sleepy asleep love".split()
with serial.Serial(PORT, BAUD, timeout=0.1) as s:
    s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False   # reset
    time.sleep(2.5); s.read(4096)
    for e in exprs + ["wake"]:
        s.write((f"expr {e}\n" if e != "wake" else "wake\n").encode())
        end = time.time() + dwell; out = b""
        while time.time() < end: out += s.read(4096)
        for line in out.decode("utf-8", "replace").splitlines():
            if line.strip(): print(f"[{e:>10}] {line}")
