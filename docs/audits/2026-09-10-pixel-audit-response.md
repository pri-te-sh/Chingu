# Response to the 2026-09-10 audit

All findings were verified against the code and accepted. Voice Lab items (F13 chunker cap, F14 first-chunk pause) were fixed in the
brain's copies; `voicelab/` itself is experimental and out of scope. Status per finding:

| # | Fix | Where |
|---|---|---|
| F01 | Simulators must present a valid session cookie on every connection and may only use simulator ids in their own household; a hello can no longer change an existing row's `device_type`/capabilities before authentication | `server.py` ws admission |
| F02 | **Device identity key**: each unit generates a 64-hex key on first boot in its own NVS namespace (survives factory reset), sends it in `hello.device_key` over verified TLS; the brain stores only its hash. A paired row with a wrong key or (legacy rows) a wrong token is closed with 4001 - no pairing code is ever handed to an unverified connection. Only an *unpaired* row is claimable; a verified unit that lost its token (factory reset / on-device un-pair) is released so its owner can re-pair. The "code wins, re-home" shortcut is gone | `server.py`, `repo.py`, `src/prefs.cpp`, `src/net.cpp`, migration 0004 |
| F03 | `/api/device/say` requires a session and a device in the caller's household | `server.py` |
| F04 | Turn/fact/follow-up mutations carry the household predicate in SQL; foreign ids are a 404 no-op | `repo.py`, `server.py` |
| F05 | `users.is_admin`; firmware upload/delete and `/api/admin/drain` require it; portal hides the FIRMWARE card for non-admins | `auth.require_admin`, `firmware.py`, migration 0004 |
| F06 | `include/secrets.h.modal.bak` removed and purged from history (force-push); `include/secrets.h*` ignored except the example; firmware no longer compiles or seeds from `secrets.h` at all - provisioning is the only path. **Owner action: rotate the Wi-Fi password; the Modal token is dead with the stopped app** | repo history, `src/prefs.cpp` |
| F07 | WebSocket uses `beginSslWithCA` and OTA uses `setCACert` with the Let's Encrypt roots (ISRG X1 + X2) baked in; SNTP runs before the first handshake | `src/certs.h`, `src/net.cpp`, `src/ota.cpp` |
| F08 | Setup form: a bare host keeps the hidden TLS/port defaults; a scheme in the input overrides | `src/net.cpp` |
| F09 | Prompt history is fetched by household **and** pixel; persona resets when a device changes household | `server.py`, `repo.pair` |
| F10 | Extraction only acts on fact/follow-up ids that were in the prompt, and all writes carry the household predicate | `memory.py` |
| F11 | Max 50 unclaimed registrations; never-claimed rows expire after 7 days (daily retention job) | `server.py`, `repo.retention` |
| F12 | Every manifest attempt sets `lastCheck_` (daily retry, no tight loop); downloads abort after 20 s without data | `src/ota.cpp` |
| F13 | Chunker enforces the 160-char cap with a whitespace fallback (test added) | `chunker.py` |
| F14 | No pause before the first chunk | `server.py` |
| F15 | Disconnect cancels and awaits the running turn; an interrupt after synthesis still sends `speech_cancel`; the simulator keeps audio sources until they end | `server.py`, portal |
| F16 | Settings are validated as a whole patch (types, ranges, timezone, colours, tone) → 400, nothing persisted | `settings.py` |
| F17 | Telemetry coerced server-side (numeric ranges, string length); portal renders numbers as numbers | `server.py`, portal |
| F18 | `/health` is liveness + readiness (db/redis/worker) with no identities; `/api/health` (authenticated) shows the household's own devices | `server.py`, `inference.ready` |
| F19 | CI deploys the exact tested SHA (`up.sh <sha>` checks it out); smoke test verifies the certificate | `.github/workflows/deploy.yml`, `deploy/up.sh` |
| F20 | Backups include the firmware volume and `.env`; loud warning when no off-site mirror; daily retention (events 90 d, logs 14 d, expired sessions) | `deploy/backup.sh`, `repo.retention` |

Tests: `tests/test_access.py` adds two-household adversarial HTTP and WebSocket cases (impersonation, code takeover, foreign
record mutation, admin gates, settings validation, public health shape). Still open from the audit's coverage list: OTA failure
paths on hardware, browser playback cancellation, end-to-end audio timing from last spoken word.


## Re-audit (`2026-09-10-pixel-reaudit.md`) — R1–R8

| # | Fix | Test |
|---|---|---|
| R1 | A legacy row (no identity key) enrols a key only on a trusted connection: valid token, or an unowned row. A paired legacy row with a bad token is closed 4001 and nothing is stored | `test_legacy_row_cannot_be_hijacked_by_planting_a_key` |
| R2 | Archive now clears `household_id` (remembering it in `prev_household_id`); an archived unit that reconnects is re-registered as unpaired and must be claimed again. The grandfather branch requires "paired, no token ever, never keyed" | `test_removed_device_loses_household_access` |
| R3 | `expire_pending` deletes only rows that were never claimed (`prev_household_id IS NULL`), have no turns and are not archived; `touch_pixel` refreshes `last_seen_at` on every authenticated connection | `test_retention_never_deletes_devices_with_history` |
| R4 | `prev_household_id` is set on unpair/archive; `pair()` resets the persona whenever the last owner differs from the new one, so unpair → claim by someone else starts clean while the owner re-pairing keeps theirs | `test_unpair_then_claim_by_another_household_resets_persona` |
| R5 | `/health` uses Redis PING and returns **503** when any part is not ready (Compose `curl -f` and the CI smoke test now fail on it) | `test_health_reports_503_when_a_dependency_is_down` |
| R6 | Backup mirrors dump + firmware archive + env to B2, exits non-zero on any partial set; `restore.sh` restores firmware volume and (optionally) env | source |
| R7 | Per-source limit: 5 anonymous registrations per IP per hour (Redis), plus the global cap; unpaired sockets are closed after 15 min (device reconnects) | source |
| R8 | Explicit `http://`/`https://` without a port selects that scheme's default port (80/443); bare host keeps hidden defaults; form hint documents `http://host:8765` for dev brains | firmware 0.4.1 |
| misc | `ws_refused` asserts a policy close code (4001/4003/4029); inter-chunk pauses counted in `audio_s`; OTA has a 10-minute total deadline | |
| hw | **Hardware OTA validation** (the audit's open item): 0.4.0/0.4.1 crashed with a stack-canary panic when downloading over TLS on the 8 KB loop task; 0.4.2 moves the download to a 16 KB task with the brain link suspended. Verified on the board: manifest over verified TLS → download 0.4.3 → SHA-256 → reboot → reconnect with identity key | serial log + brain events |


## Verification pass 3 (`2026-09-10-pixel-verification-3.md`) — V1, V2

| # | Fix | Test |
|---|---|---|
| V1 | Sessions carry an ownership snapshot (household, token hash, archived). It is re-checked before every text/audio turn and every ~5 s by the injector; a mismatch closes the socket 4001. `revoke_sessions()` closes any live socket for a device immediately on: device-initiated release, portal un-pair, portal remove, and a successful claim. A new authenticated connection for the same device supersedes the old one (4000). | `test_live_socket_is_revoked_when_device_changes_hands` |
| V2 | The credential-free grandfather branch is removed: a paired row with no token is refused unless the unit proves its identity key. Production check: zero token-less physical rows existed. | `test_tokenless_legacy_row_is_not_authenticated_by_device_id_alone` |
