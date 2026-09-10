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
