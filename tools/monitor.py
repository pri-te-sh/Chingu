#!/usr/bin/env python3
"""Non-interactive serial monitor: resets the board, then prints output for N seconds.
Usage: .venv/bin/python tools/monitor.py [seconds] [--no-reset] [--send "cmd" [--after S]]"""
import sys, time, serial

import glob
PORT = (glob.glob("/dev/cu.*usbserial*") or ["/dev/cu.usbserial-110"])[0]
BAUD = 115200
secs = float(next((a for a in sys.argv[1:] if not a.startswith("--")), 6))
reset = "--no-reset" not in sys.argv
send = sys.argv[sys.argv.index("--send") + 1] if "--send" in sys.argv else None
after = float(sys.argv[sys.argv.index("--after") + 1]) if "--after" in sys.argv else 4.0

with serial.Serial(PORT, BAUD, timeout=0.2) as s:
    if reset:                     # CH340 auto-reset circuit: pulse EN via RTS while DTR low
        s.dtr = False; s.rts = True; time.sleep(0.1); s.rts = False
    end = time.time() + secs
    buf = b""; sent = False
    while time.time() < end:
        buf += s.read(4096)
        if send and not sent and time.time() > end - secs + after:
            s.write((send + "\n").encode()); sent = True
    sys.stdout.write(buf.decode("utf-8", "replace"))
