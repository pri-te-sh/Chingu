"""Firmware releases: upload/publish builds, serve binaries, manifest for devices, push "update now" to a Pixel.

Binaries live on disk (PIXEL_FIRMWARE_DIR, a Docker volume in Compose); metadata in `firmware_releases`.
A device asks GET /api/firmware/manifest?device_type=lite&channel=stable and gets the newest release
(url + sha256); it verifies the hash before flashing. Devices have no session cookie, so the manifest
and the binaries are public - they are not secrets, the hash is what protects the device.
"""
import hashlib, os, re
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from . import auth, bus, models as m, repo

router = APIRouter()
FW_DIR = Path(os.environ.get("PIXEL_FIRMWARE_DIR", Path(__file__).resolve().parent.parent / "data" / "firmware"))
PUBLIC_URL = os.environ.get("PIXEL_PUBLIC_URL", "").rstrip("/")     # e.g. https://pixel.example.com; empty = derive from the request
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DEVICE_TYPES = ("lite", "3s")


def vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def base_url(request: Request) -> str:
    return PUBLIC_URL or str(request.base_url).rstrip("/")


def bin_url(request: Request, r: dict) -> str:
    return f"{base_url(request)}/firmware/{r['device_type']}/{r['channel']}/{r['version']}.bin"


async def releases(device_type: str | None = None, channel: str | None = None) -> list[dict]:
    q = sa.select(m.firmware_releases)
    if device_type: q = q.where(m.firmware_releases.c.device_type == device_type)
    if channel: q = q.where(m.firmware_releases.c.channel == channel)
    rows = await repo.fetch_all(q)
    return sorted(rows, key=lambda r: (r["device_type"], r["channel"], vkey(r["version"])), reverse=True)


async def latest(device_type: str, channel: str = "stable") -> dict | None:
    rs = await releases(device_type, channel)
    return rs[0] if rs else None


def public(r: dict, request: Request) -> dict:
    return {"id": r["id"], "device_type": r["device_type"], "channel": r["channel"], "version": r["version"], "sha256": r["sha256"],
            "size": r["size"], "notes": r.get("notes") or "", "created_at": r.get("created_at"), "url": bin_url(request, r)}


@router.get("/api/firmware/manifest")
async def manifest(request: Request, device_type: str = "lite", channel: str = "stable", current: str = ""):
    r = await latest(device_type, channel)
    if not r: return {}
    out = public(r, request)
    out["update"] = bool(current and VERSION_RE.match(current) and vkey(r["version"]) > vkey(current))
    return out


@router.get("/firmware/{device_type}/{channel}/{version}.bin")
async def download(device_type: str, channel: str, version: str):
    if device_type not in DEVICE_TYPES or not VERSION_RE.match(version) or not re.match(r"^[a-z]+$", channel): raise HTTPException(404)
    path = FW_DIR / device_type / channel / f"{version}.bin"
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path, media_type="application/octet-stream", filename=f"pixel-{device_type}-{version}.bin")


@router.get("/api/firmware/releases")
async def api_releases(request: Request):
    await auth.require_user(request)
    return [public(r, request) for r in await releases()]


async def store(data: bytes, device_type: str, channel: str, version: str, notes: str) -> dict:
    if device_type not in DEVICE_TYPES: raise HTTPException(400, "device_type must be lite or 3s")
    if not VERSION_RE.match(version): raise HTTPException(400, "version must look like 1.2.3")
    if not re.match(r"^[a-z]+$", channel): raise HTTPException(400, "channel must be lowercase letters")
    if len(data) < 100_000 or data[0] != 0xE9: raise HTTPException(400, "that is not an ESP32 firmware image (.bin)")
    if await repo.fetch_one(sa.select(m.firmware_releases).where(m.firmware_releases.c.device_type == device_type, m.firmware_releases.c.channel == channel, m.firmware_releases.c.version == version)):
        raise HTTPException(409, f"{device_type} {version} already exists on {channel}")
    d = FW_DIR / device_type / channel; d.mkdir(parents=True, exist_ok=True)
    (d / f"{version}.bin").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    await repo.execute(sa.insert(m.firmware_releases).values(device_type=device_type, channel=channel, version=version, url=f"/firmware/{device_type}/{channel}/{version}.bin", sha256=sha, size=len(data), notes=notes[:500]))
    return await repo.fetch_one(sa.select(m.firmware_releases).where(m.firmware_releases.c.device_type == device_type, m.firmware_releases.c.channel == channel, m.firmware_releases.c.version == version))


@router.post("/api/firmware/upload")
async def upload(request: Request, file: UploadFile = File(...), device_type: str = Form("lite"), channel: str = Form("stable"), version: str = Form(...), notes: str = Form("")):
    await auth.require_admin(request)
    r = await store(await file.read(), device_type, channel, version.strip(), notes)
    return public(r, request)


@router.delete("/api/firmware/releases/{rid}")
async def delete_release(request: Request, rid: int):
    await auth.require_admin(request)
    r = await repo.fetch_one(sa.select(m.firmware_releases).where(m.firmware_releases.c.id == rid))
    if not r: raise HTTPException(404)
    p = FW_DIR / r["device_type"] / r["channel"] / f"{r['version']}.bin"
    if p.exists(): p.unlink()
    await repo.execute(sa.delete(m.firmware_releases).where(m.firmware_releases.c.id == rid))
    return {"ok": True}


@router.post("/api/pixels/{pid}/update")
async def push_update(request: Request, pid: int):
    """Portal 'Update now': the device re-checks the manifest itself and installs if newer."""
    user = await auth.require_user(request)
    hs = await auth.households_for(user["id"])
    p = await repo.pixel(pid)
    if not p or p["household_id"] not in [h["id"] for h in hs]: raise HTTPException(404)
    if p["device_type"] == "sim": raise HTTPException(400, "the simulator has no firmware")
    r = await latest(p["device_type"], p.get("fw_channel") or "stable")
    if not r: raise HTTPException(404, "no firmware has been published for this device type")
    if p.get("fw_version") and VERSION_RE.match(p["fw_version"]) and vkey(p["fw_version"]) >= vkey(r["version"]): raise HTTPException(409, f"already on {p['fw_version']}")
    present = await bus.presence_all([p["device_id"]])
    if p["device_id"] not in present: raise HTTPException(409, "Pixel is offline")
    await bus.inbox_push(p["device_id"], {"type": "ota", "version": r["version"]})
    await repo.device_event(pid, "ota_requested", version=r["version"])
    return {"ok": True, "version": r["version"]}
