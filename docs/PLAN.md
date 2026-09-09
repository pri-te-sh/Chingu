# Pixel Platform — Plan & Task Tracker

_Living document. Claude works top-down through the unchecked tasks and updates this file as things land.
Last updated: 2026-09-09._

## Decisions (settled — do not re-open without a reason)

| # | Decision |
|---|---|
| D1 | **Two device types**: **Pixel-Lite** (Hosyond 3.2" ESP32, current) and **Pixel-3S** (Waveshare ESP32-S3-Touch-LCD-3.5B, ordered). Plus **sim** (browser simulator). One firmware codebase, board HAL per target. |
| D2 | **Backend = Python/FastAPI** (audio pipeline is Python). Persistence **Postgres**, realtime fan-out/sessions **Redis**. SQLAlchemy Core + Alembic for schema/migrations only where it earns its keep — plain SQL is fine. No Neon, no Modal Dict. |
| D3 | **Hosting**: local **Docker Compose** first (Mac), then the same Compose on an **Oracle Cloud Always-Free ARM VM** (2 OCPU / 12 GB). Multi-arch images (amd64 + arm64). Modal stays live only until cutover, then retired (may return later as optional GPU inference worker). |
| D4 | **Auth**: pluggable. `dev` provider (email/password from `.env`, only when `PIXEL_ENV=dev`) now; **Google sign-in** at cutover (needs a Google OAuth client — 10-min console task, deferred). |
| D5 | **Memory scope**: facts + follow-ups → **owner**; name/personality/voice/eyes/mood tints → **Pixel**; chat history → **Pixel**; daily summary → **owner**. |
| D6 | **Households** modelled now (many accounts ↔ many Pixels); UI stays single-owner for now. |
| D7 | **Audio is never stored**; transcripts only, deletable. |
| D8 | **Camera: all in** — presence, face detection/tracking, face recognition (owner vs known people), "look at this" snapshots to the cloud vision model. Portal toggles + on-face indicator whenever the camera is active. Recognition embeddings stored per household; raw frames not retained beyond the request. |
| D9 | **Docs**: work only on Pixel-Lite until the 3S hardware arrives; download the Waveshare 3.5B pack then. |
| D10 | Device access code stays **device-only** (6 chars, shown on the device). Becomes the **pairing code**. |

## Architecture (target)

```
Pixel-Lite / Pixel-3S / sim ──wss──► Caddy ─► brain (FastAPI, async, WS sessions, REST, portal, auth)
                                              ├── Postgres  (users, households, pixels, personas, memory, turns, events, plant readings)
                                              ├── Redis     (device presence, per-device inbox, pub/sub, sessions, rate limits)
                                              └── worker    (faster-whisper STT, Piper TTS; separate process so the WS loop never blocks)
                                     Uptime Kuma (health alerts) · nightly pg_dump → Backblaze B2
```

## Data model (v1)

- `users` (id, provider, provider_id, email, name, avatar, created_at)
- `households` (id, name, owner_user_id) · `household_members` (household_id, user_id, role)
- `pixels` (id, household_id, device_type lite|3s|sim, name, pairing_code, token_hash, capabilities jsonb, created_at, last_seen_at)
- `personas` (pixel_id, persona_text, tone jsonb, eye_color, mood_colors jsonb, auto_sleep_s, chat_model, memory_model, think flags, tools flags, barge-in settings, location, interests, timezone)
- `facts` (id, household_id, type, text, first_seen, last_confirmed, source_turn_id, pinned, archived)
- `followups` (id, household_id, text, due, created, done)
- `summaries` (household_id, day, text)
- `turns` (id, pixel_id, ts, user_text, reply, expr, intensity, t_expr, t_audio, t_done, audio_s, model, tools jsonb, steps jsonb)
- `device_events` (id, pixel_id, ts, event, meta jsonb)
- `ambient` (household_id, ts, brief jsonb)
- `plant_nodes` (id, household_id, name, token_hash) · `plant_readings` (node_id, ts, moisture, soil_temp, air_temp, rh, lux, co2)
- `faces` (id, household_id, label, embedding, created_at) — for D8

## Phases & tasks

### P0 — Local platform spine (Docker Compose, Postgres, Redis)
- [ ] `docker-compose.yml`: caddy, brain, worker, postgres, redis (+ uptime-kuma profile); `.env.example`; multi-arch Dockerfiles
- [ ] Postgres schema + Alembic migrations for the v1 data model
- [ ] Store layer: replace `store.py` JSON/Dict with repository functions over Postgres; Redis for presence/inbox/pubsub
- [ ] One-shot importer: Modal Dict → Postgres (facts, follow-ups, summaries, turns, events, config → persona)
- [ ] Worker process: STT/TTS over a local queue (Redis) so inference never blocks the WS loop
- [ ] pytest suite (store, memory scoping, protocol) runnable in Compose
- [ ] Simulator + current board work against `localhost` Compose end to end (face, tools, memory, ambient)

### P1 — Auth & profiles
- [ ] Auth provider interface; `dev` provider (env credentials); session cookies; CSRF for portal POSTs
- [ ] `users`, `households`, membership on first login
- [ ] Portal: login page, PROFILE page (name, avatar, sign out), household switcher stub
- [ ] Google provider (Authlib) wired but disabled until cutover

### P2 — Multi-Pixel & pairing
- [ ] Pairing flow: device shows code → portal "Add a Pixel" → claim → per-device token issued and pushed; revoke from portal
- [ ] `hello` carries `device_type`, `fw`, `capabilities` (mic, speaker, camera, imu, battery); brain adapts features
- [ ] Portal Pixel switcher; DEVICE page per Pixel; DASHBOARD shows all Pixels of the household
- [ ] Persona per Pixel; memory per owner (D5) — prompt builder reads both
- [ ] Firmware (Pixel-Lite): pairing message + token storage in NVS; capabilities in hello; QR points at `/pair?code=…`

### P3 — Cutover to Oracle
- [ ] Oracle A1 VM (Ubuntu 24.04 arm64), PAYG upgrade, firewall; `bootstrap.sh` (docker, fail2ban, unattended-upgrades, clone, compose up)
- [ ] Domain + Caddy TLS; Google OAuth client; `.env` on the box
- [ ] Nightly `pg_dump` → Backblaze B2; weekly restore test; Uptime Kuma alerts
- [ ] Import prod data from Modal; repoint device (`secrets.h`) and simulator; retire Modal app

### P4 — Pixel-3S bring-up (after hardware arrives)
- [ ] Download Waveshare 3.5B docs/demo pack; verify camera FPC orientation from schematic
- [ ] Firmware HAL split: `boards/lite`, `boards/3s`; PlatformIO envs
- [ ] 3S: AXS15231B QSPI display + capacitive touch; face engine at 320×480
- [ ] 3S: ES8311 mic/speaker via I²S; esp-sr AFE (AEC + NS) → full-duplex barge-in; wake word "Hey Pixel"
- [ ] 3S: OV2640 camera → presence, face tracking (eyes follow), face recognition, "look at this"
- [ ] 3S: IMU gestures (pick-up, tap), battery guard (warn 3.55 V, deep-sleep 3.45 V), battery in heartbeat
- [ ] Pixel-Lite: speaker via DAC + half-duplex gating (no mic on Lite unless INMP441 added)

### P5 — PotBot
- [ ] XIAO ESP32-C3 node firmware: SHT40, VEML7700, STEMMA soil → readings every 5 min
- [ ] Brain: plant readings, thresholds, plant section in memory/ambient; PLANT portal page with sparklines and calibration
- [ ] Pixel behaviours: mentions plant state naturally; camera time-lapse later

## Cutover checklist (P3)
1. Compose up on Oracle, health green, Uptime Kuma pinging
2. Import prod data; verify facts/turns in portal
3. Flash device with new host + pairing; pair in portal
4. Simulator on new host; run pipeline test P1–P5
5. Watch 24 h; then `modal app stop pixel-brain`

## Open questions
- Speaker/mic for Pixel-Lite: add INMP441 later, or leave Lite as face + speaker only?
- Domain name.
