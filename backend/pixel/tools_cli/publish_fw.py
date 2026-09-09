"""Publish a firmware build as a release (runs inside the brain container, no login needed).
Usage:  docker compose exec -T brain python -m pixel.tools_cli.publish_fw --version 0.3.0 --device-type lite [--channel stable] [--notes "..."] < firmware.bin"""
import argparse, asyncio, sys
from pixel import firmware

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True); ap.add_argument("--device-type", default="lite"); ap.add_argument("--channel", default="stable"); ap.add_argument("--notes", default="")
    a = ap.parse_args()
    data = sys.stdin.buffer.read()
    r = await firmware.store(data, a.device_type, a.channel, a.version, a.notes)
    print(f"published {r['device_type']} {r['version']} on {r['channel']}: {r['size']} bytes sha256 {r['sha256'][:12]}...")

if __name__ == "__main__": asyncio.run(main())
