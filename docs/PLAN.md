# Pixel Platform — Plan & Task Tracker

_Living document. Claude works top-down through the unchecked tasks and updates this file as things land.
Last updated: 2026-09-09 (P0 + P1 + most of P2 done locally; Modal frozen at its last deploy — `modal_app.py` is no longer deployable from this tree and is retired at P3)._

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
| D11 | **Provisioning**: no secrets baked into firmware. First boot (or long-press reset) → device opens a SoftAP captive portal `Pixel-XXXX` (works from any phone), user picks home Wi-Fi; brain URL defaults to the platform domain (editable for dev/self-host). Device then connects **unpaired**, shows its pairing code on the face; owner claims it in the portal → brain issues a **per-device token** (NVS, rotatable, revocable). Also an **ESP Web Tools** page in the portal for first-time flashing over USB from Chrome. |
| D12 | **OTA**: HTTPS pull with rollback. Brain serves signed-by-hash manifests per `device_type` × `channel` (stable/beta); device checks on boot + daily + on push, downloads to the inactive slot, verifies SHA-256, reboots, and marks the image valid only after a successful brain connection (else auto-rollback). Firmware built by CI on tags, uploaded with the manifest. Portal shows version per Pixel, Update now, channel, progress. Pixel-Lite uses the `min_spiffs` partition table (2 × 1.9 MB app slots on 4 MB flash). |
| D13 | **Telemetry**: device heartbeat grows (fw version, reset reason, min heap, fps, RSSI, reconnects, audio under/overruns, battery); device log ring shipped to the brain on request or on error (never audio/transcripts); crash reason + backtrace uploaded on the boot after a panic. Brain: structured JSON logs with turn/pixel ids, per-stage latency (stt / llm-first-token / tts-first-audio / done) and error counters stored per turn and rolled up; `/metrics` (Prometheus format) for later Grafana; **HEALTH** portal page per Pixel and for the brain. Optional Sentry/GlitchTip via env. Retention: device logs 14 d, events 90 d, turns until the owner deletes. |
| D14 | **Secrets & tokens**: device tokens hashed at rest; portal sessions server-side; all secrets in `.env`; tokens redacted from every log line. |

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
- `firmware_releases` (id, device_type, channel, version, url, sha256, size, min_version, notes, created_at) · `pixels.fw_version`, `pixels.fw_channel`, `pixels.reset_reason`
- `device_logs` (pixel_id, ts, level, line) — 14-day retention · `turn_metrics` rollups (pixel_id, hour, p50/p95 per stage, errors, tool_calls)

## Phases & tasks

### P0 — Local platform spine (Docker Compose, Postgres, Redis)
- [x] `docker-compose.yml`: caddy, brain, worker, postgres, redis (+ uptime-kuma profile); `.env.example`; multi-arch Dockerfiles
- [x] Postgres schema + Alembic migrations for the v1 data model
- [x] Store layer: replace `store.py` JSON/Dict with repository functions over Postgres; Redis for presence/inbox/pubsub
- [x] One-shot importer: Modal Dict → Postgres (facts, follow-ups, summaries, turns, events, config → persona)
- [x] Worker process: STT/TTS as a separate HTTP service (`pixel/worker.py`) so inference never blocks the WS loop (swap-able for GPU/hosted later)
- [x] Structured JSON logging (structlog) with pixel/turn ids; per-stage latency + error counters recorded per turn; `/metrics` endpoint
- [x] pytest suite (store, memory scoping, protocol) runnable in Compose
- [x] Simulator + current board work against `localhost` Compose end to end (face, tools, memory, ambient)

### P1 — Auth & profiles
- [x] Auth provider interface; `dev` provider (env credentials); session cookies; CSRF for portal POSTs
- [x] `users`, `households`, membership on first login
- [x] Portal: login page, PROFILE page (name, avatar, sign out, household name, members, Pixels) — switcher deferred to P2
- [x] Google provider (Authlib) wired but disabled until cutover

### P2 — Multi-Pixel & pairing
- [x] Firmware: start the turn timer on `transcript` so portal-initiated turns report real latencies (currently bogus on inbox turns)
- [x] Pairing flow: device shows code → portal "Add a Pixel" → claim → per-device token issued and pushed; revoke from portal
- [x] `hello` carries `device_type`, `fw`, `capabilities` (mic, speaker, camera, imu, battery); brain adapts features
- [x] Portal restructured (2026-09-09): HOME / TALK / HISTORY / MEMORY / PIXELS (cards + per-Pixel tabs: identity, voice & brain, eyes, device) / HOUSEHOLD / account menu; Pixel switcher in header; Add-a-Pixel wizard
- [x] Removing a Pixel archives it (unpair + hide, history kept) — never cascade-deletes turns
- [x] Persona per Pixel; memory per owner (D5) — prompt builder reads both
- [x] Firmware (Pixel-Lite): pairing message + token storage in NVS; capabilities in hello; device id from MAC (`lite-xxxxxx`); Portal screen shows pairing code
- [x] Firmware: SoftAP captive-portal provisioning (Wi-Fi + brain URL), "setup" face, long-press-to-reset; remove `secrets.h` dependency
- [x] Portal: "Add a Pixel" (code + name) on PROFILE — full wizard copy + ESP Web Tools flasher page still to do
- [ ] Portal: setup wizard copy + ESP Web Tools flasher page
- [x] Provisioning: captive portal verified on iPhone 2026-09-09 (no Android device available; the page also works by visiting 192.168.4.1 directly, which the device screen says)

### P2b — OTA updates
- [x] Pixel-Lite partition table → `min_spiffs.csv` (two 1.9 MB app slots; image 1.13 MB = 57%); flashed once over USB 2026-09-09
- [x] Firmware updater `src/ota.*`: manifest check (90 s after boot, daily, on `{"type":"ota"}` push, Update screen "Check now"), download to inactive slot, SHA-256 verify, reboot, mark-healthy on brain `ready`, self-rollback after 3 min otherwise; full-screen progress; `PIXEL_FW_VERSION` in platformio.ini is the version of record (bump it every release)
- [x] Brain `pixel/firmware.py`: releases on the `firmware` volume, `GET /api/firmware/manifest`, `GET /firmware/{type}/{channel}/{ver}.bin`, portal upload `POST /api/firmware/upload`, `POST /api/pixels/{id}/update` push; `tools/release.sh <ver> "<notes>"` builds + publishes; verified 0.3.0 → 0.3.1 OTA on the board 2026-09-09
- [x] Portal: PIXELS > FIRMWARE card (releases, publish a .bin), DEVICE tab shows installed/built/latest + UPDATE NOW with progress
- [ ] CI (GitHub Actions): build on tag, publish binaries to the brain (needs P3 public URL)
- [x] Device settings pass: 3×3 menu (+ Update tile with badge), every value clipped with "..." (`fit()`), Portal/Setup hints wrapped inside 320×240, System shows firmware + device id instead of the serial cheat-sheet

### P2c — Voice quality lab (before hosting)  → `docs/VOICE_LAB.md`
- [x] `voicelab/` scaffold + bench CLI: STT (faster-whisper base/small/distil, Parakeet-TDT 0.6B), TTS CPU (Piper, Kokoro, Supertonic, Pocket TTS), TTS on Modal GPU (Kokoro, Chatterbox, Qwen3-TTS, VoxCPM, CosyVoice3, Fish scaffold), VAD energy vs Silero
- [x] Test-bed web app: HEAR / SPEAK / TALK (chunk pipeline visualised inline, pauses, echo-proof barge-in) / RESULTS
- [x] **Decision 2026-09-10: keep Piper for now; Modal apps stopped; lab parked.** Full findings in `docs/VOICE_LAB.md`
- [x] Ported to the brain 2026-09-10: clause chunker (first clause early, ≤160 chars, all boundaries), queued speaker task (TTS overlaps LLM streaming), punctuation pauses (140/320 ms), per-chunk `chunk` events shown inline in the simulator, mid-reply expression tags stripped from speech
- [x] Parakeet-TDT 0.6B STT in the worker (`PIXEL_STT_ENGINE=parakeet`, whisper auto-fallback, per-request override; simulator has an STT selector and shows engine + ms on each transcript). In-container A/B on synthetic speech: whisper 1.9 s / WER 0.05 vs Parakeet 290 ms / WER 0.03. Enabled in production 2026-09-10; validate with real voice on the simulator

### P3a — Security audit remediation (2026-09-10) → `docs/audits/2026-09-10-pixel-audit.md` + `-response.md`
- [x] F01–F20 fixed in one release: device identity keys (survive factory reset; hash stored), cookie-authenticated simulators, no pairing-code takeover (only unpaired rows claimable; verified units that lost their token are released), household-scoped record writes, admin-only firmware/drain (`users.is_admin`), validated settings, minimal public `/health`, retention job, pinned-SHA deploys, fuller backups; firmware 0.4.0: verified TLS with Let's Encrypt roots + SNTP, setup-form TLS fix, OTA backoff/stall deadline, no `secrets.h` seeding; secrets backup purged from git history
- [ ] Owner: rotate the home Wi-Fi password (it was in the purged file); optionally ask GitHub support to gc the old objects
- [x] Re-audit R1–R8 fixed (legacy key enrolment, archive revocation, retention safety, persona reset on transfer, 503 health, full backup set, per-IP registration limit, setup-form ports) — 26 tests
- [x] OTA over verified TLS validated on hardware (0.4.2 → 0.4.3); found and fixed a loop-task stack overflow in the download path
- [x] Verification pass 3: live-socket revocation on ownership change (V1), grandfather branch removed (V2) — 28 tests
- [ ] Still open: browser playback-cancellation test, end-to-end audio timing from last spoken word; household invites so a second Google account can join "Home"

### P3 — Cutover to a VM (Hetzner CX23 or home box + Cloudflare Tunnel; Oracle dropped)
- [x] **Hetzner CX23** (Ubuntu 26.04, 2 vCPU/4 GB, 2.29.41.213, SSH key "hetzner") bootstrapped 2026-09-10: docker, ufw 22/80/443, fail2ban, unattended-upgrades, `pixel` user, stack up with prod profile, laptop DB restored (104 turns, 3 pixels), nightly backups cron
- [x] DNS `pixel.priteshbhavsar.com` → 2.29.41.213 (Netlify), Let's Encrypt cert via Caddy 2026-09-10
- [x] Google sign-in 2026-09-10: GCP project `pixel-companion-508203`, consent screen "Pixel Companion" (External, **Testing** — only listed test users; "Pixel" alone is rejected as a Google trademark), web client with redirect `https://pixel.priteshbhavsar.com/auth/callback`; creds in the server `.env`; dev login still allowed (PIXEL_ALLOW_DEV_LOGIN=1) until Google login is confirmed, then set it to 0
- [x] CI/CD live 2026-09-10: `.github/workflows/deploy.yml` — tests on Postgres/Redis services, then SSH `deploy/up.sh` as `pixel` + HTTPS smoke test (secrets DEPLOY_HOST + DEPLOY_SSH_KEY = `~/.ssh/pixel_deploy`); first green run #34433150311
- [ ] Nightly `pg_dump` → Backblaze B2; weekly restore test; Uptime Kuma alerts
- [ ] Telemetry: heartbeat v2 (fw, reset reason, min heap, fps, RSSI, reconnects, audio xruns, battery); device log shipping + crash upload; HEALTH portal page (per Pixel + brain latency percentiles); retention jobs
- [x] Board repointed with `POST /api/pixels/21/brain` (firmware 0.3.2) 2026-09-10 — reconnects once the home router's negative DNS cache (1 h) expires; Modal `pixel-brain` app stopped
- [x] Board `lite-0365e8` online on the new brain 2026-09-10 23:59 (after the router's negative DNS cache expired); laptop Compose stack stopped (`docker compose up -d` in backend/ brings the dev stack back)
- [x] Landing page for signed-out visitors (animated face, typewriter, motes, feature tiles; Google-only sign-in, dev login off in prod)

### P4 — Pixel-3S bring-up (after hardware arrives)
- [ ] Download Waveshare 3.5B docs/demo pack; verify camera FPC orientation from schematic
- [ ] Firmware HAL split: `boards/lite`, `boards/3s`; PlatformIO envs
- [ ] 3S: AXS15231B QSPI display + capacitive touch; face engine at 320×480
- [ ] 3S: ES8311 mic/speaker via I²S; esp-sr AFE (AEC + NS) → full-duplex barge-in; wake word "Hey Pixel"
- [ ] 3S: OV2640 camera → presence, face tracking (eyes follow), face recognition, "look at this"
- [ ] 3S: IMU gestures (pick-up, tap), battery guard (warn 3.55 V, deep-sleep 3.45 V), battery in heartbeat
- [ ] Pixel-Lite: speaker via DAC (IO26, amp IO4) + INMP441 I²S mic (SCK IO25, WS IO32, SD IO35 — arrives 2026-09-10) with half-duplex gating (no AEC on classic ESP32); wake word via server-side openWakeWord or push-to-talk touch

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
- Domain name.
