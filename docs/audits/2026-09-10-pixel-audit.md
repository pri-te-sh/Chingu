# Pixel project audit — 2026-09-10

**Assessment: working prototype, with release-blocking authentication and household-isolation defects.** Passing tests and a successful firmware build do not establish safe public operation.

Scope: local backend, portal, firmware networking/provisioning/OTA, persistence, authentication, deployment scripts/CI, tests, roadmap, and saved Voice Lab results. Read-only audit of application code; no fixes, deployments, hardware flashing, live-service exploitation, or credential rotation performed. This is not an exhaustive dependency/CVE audit or a hardware qualification.

The checkout advanced during review from `5eb533d` (with uncommitted pipeline changes) to `3830251`. The pipeline changes were committed externally during the audit; this report includes them. Findings reference the final files. Credentials are deliberately omitted.

## Critical/high findings

### F01 — P0: WebSocket device authentication can be bypassed
Evidence: `backend/pixel/server.py:241`, `backend/pixel/repo.py:73`.

The client chooses `device_type`; `get_or_create_pixel` persists that type before authentication. A client naming an existing paired device and declaring `sim` bypasses the failed-token branch. Already-paired simulators do not require a session cookie or household ownership check. This grants a conversation session with the victim's memory/persona, permits memory writes through tools, and can change a physical device's stored type. Device IDs are also exposed by public health responses.

Validation: a local TestClient WebSocket with a mocked paired repository row reached `ready` without a cookie or token. The physical-to-sim type mutation is verified by source inspection. Authenticate before registration updates; authorize every simulator connection against its actual household; do not trust hello metadata as authorization.

### F02 — P0: Pairing fallback permits remote device takeover
Evidence: `backend/pixel/server.py:263`, `backend/pixel/server.py:665`, `backend/pixel/repo.py:81`.

A normal reconnect recreates a pairing code even on a paired device. A wrong-token connection receives that code over the network. The claim endpoint explicitly permits moving a device from its existing household using the code. Thus the code does not prove physical possession: another signed-in user can obtain it remotely, rehome the device, and rotate its token. The unauthenticated pairing connection also drains the same device inbox used by its valid session, creating command/token delivery races.

Validation: isolated database check confirmed a paired device regains a code on reconnect; the disclosure and cross-household claim paths were inspected. Reject invalid tokens for paired devices; permit reset/re-pairing through a separately authenticated or physically confirmed flow; use a distinct pairing session/channel.

### F03 — P1: Device “say” is unauthenticated and unscoped
Evidence: `backend/pixel/server.py:735`.

`POST /api/device/say` never invokes authentication or household scoping. An anonymous caller can queue arbitrary conversation input for a specified online device, or the first globally online device by omitting its ID. This can trigger model/tool usage and memory changes.

Validation: local ASGI request returned HTTP 200 without cookies or CSRF header and invoked a mocked inbox push. Require login and device ownership before looking up presence or enqueueing.

### F04 — P1: Record mutation endpoints cross household boundaries
Evidence: `backend/pixel/server.py:584`, `:598`, `:605`, `:618`, `:623`; corresponding ID-only repository writes in `backend/pixel/repo.py`.

Turn deletion, fact update/deletion, and follow-up update/deletion check only whether someone is signed in. Sequential record IDs are accepted without checking ownership. Fact update also returns the foreign fact row.

Validation: two users in an isolated database; user B successfully changed user A's fact through the real HTTP route. Apply household predicates to every read/update/delete, ideally in repository functions as well as route guards.

### F05 — P1: Any account can manage platform-wide firmware and drain devices
Evidence: `backend/pixel/firmware.py:92`, `:99`; `backend/pixel/server.py:723`.

Upload/delete firmware and `/api/admin/drain` require only an ordinary user session. Firmware releases are global across households. A normal account can publish a newer release for everyone, delete existing releases, or disconnect all present devices. Publishing alone does not automatically install firmware, but poisons subsequent user-triggered updates.

Use an explicit platform-admin/publisher authorization policy, separate from household ownership.

### F06 — P1: Credentials are committed; local builds can embed secrets
Evidence: tracked `include/secrets.h.modal.bak:3`, `:4`, `:11`; `src/prefs.cpp:5`, `:55`; `.gitignore`.

The tracked backup contains nonempty Wi-Fi SSID/password and backend-token literals. Their present validity was not tested. Ignoring `include/secrets.h` does not exclude its backup. In addition, firmware conditionally compiles the real secrets header and seeds credentials on first boot, contradicting the “no secrets baked into firmware” release requirement.

Rotate affected credentials as appropriate, remove the backup and address repository history exposure, and make release builds reject credential headers. No secret values are reproduced here.

### F07 — P1: TLS does not authenticate the brain or OTA source
Evidence: `src/ota.cpp:72`, `:99`; `src/net.cpp:205`; installed WebSockets library `WebSocketsClient.cpp:295`.

OTA explicitly calls `setInsecure()`. The WebSocket uses `beginSSL` without a CA or fingerprint; the installed library follows its `setInsecure()` branch. A network intermediary can impersonate the brain, intercept device credentials/conversations, or replace both the firmware and the matching manifest hash. SHA-256 checks integrity against the received manifest; it is not a publisher signature.

Configure trusted certificates/CA verification and authenticated manifests or firmware signatures. The library behavior was checked locally; no interception was attempted.

### F08 — P1: Default provisioning disables TLS
Evidence: `src/net.cpp:60`, `:71`.

The setup form fills the host with the bare domain, but save sets TLS solely from whether the input starts with `https://`. Saving the default form changes TLS from true to false while retaining hidden port 443, so the device attempts plain WebSocket against an HTTPS listener. The hidden TLS input is ignored.

Preserve the selected/default TLS setting for bare hosts, or prefill and parse a complete URL; validate host, port and scheme together.

### F09 — P1: Rehomed devices expose previous-owner conversation context
Evidence: `backend/pixel/server.py:90`; `backend/pixel/repo.py:106`, `:188`.

Pairing changes the pixel's household without changing its identity or separating conversation history. Prompt history is fetched by pixel ID alone, so the next household's conversation includes turns from the previous household. Archived devices that are registered and claimed again have the same issue. Persona settings also survive reassignment.

Validation: isolated database rehome retained the old owner's turn in the same query used to build prompts. Scope history by both household and pixel, and define a clean ownership-transfer boundary for persona/device state.

### F10 — P1: Memory extraction trusts model-supplied foreign IDs
Evidence: `backend/pixel/memory.py:150`, `:161`; `backend/pixel/repo.py:147`.

Generated JSON controls fact IDs passed to confirm/update and follow-up IDs passed to resolve. Those writes have no household predicate and the IDs are not checked against the source household's records. A model mistake or manipulated extraction output can mutate another household's data. This remains a separate path after fixing REST authorization.

Validate generated IDs against the household-owned input set and enforce ownership in SQL. Source-confirmed; a successful prompt-injection attack was not attempted.

## Reliability, privacy and operational findings

### F11 — P1: Unauthenticated WebSocket registration has no application quota
Evidence: `backend/pixel/server.py:241`, `backend/pixel/repo.py:86`, `backend/Caddyfile`.

Every new hello device ID can allocate persistent pixel/persona rows before authentication, then hold a pairing connection. There is no application-level registration rate limit or pending-device expiry. Repeated requests can grow the database and connection workload. No load test was performed. Add bounded pending registration, expiry, per-source limits and maximum concurrent pending sessions.

### F12 — P2: OTA retries and downloads can monopolize the firmware loop
Evidence: `src/ota.cpp:74`, `:80`, `:85`, `:109`, `:141`.

`lastCheck_` advances only for a populated successful manifest. Failed checks and an empty release list therefore retry every main-loop iteration once the initial 90 seconds expires. Each request can block for seconds. The download loop also waits indefinitely when connected but receiving no bytes; its `delay(2)` branch has no elapsed-time deadline. Add retry backoff and explicit idle/overall download deadlines.

### F13 — P2: The chunker does not enforce its advertised 160-character cap
Evidence: `backend/pixel/chunker.py:14`; same algorithm in `voicelab/voicelab/chunker.py`.

Long unpunctuated output has no fallback split at whitespace. A distant sentence boundary is accepted without checking the maximum. This delays first speech and submits oversized TTS chunks.

Validation: 100 repetitions of “word ” followed by a period emitted one 501-character chunk. The existing short examples pass but do not test this boundary. Implement a hard whitespace/character fallback.

### F14 — P2: The new speaker pipeline inserts silence before the first chunk
Evidence: `backend/pixel/server.py:106`, `:117`.

`last_end` begins as the empty string, and Python evaluates `'' in '.!?'` as true. Every first chunk therefore receives a 320 ms silence prefix. First-audio telemetry is recorded before that prefix, so the displayed latency understates audible response time. Pause bytes are also excluded from `audio_s`.

Require a nonempty previous ending, include inserted audio in duration, and distinguish emitted bytes from audible speech timing.

### F15 — P2: Disconnects and late interrupts do not consistently cancel playback/work
Evidence: `backend/pixel/server.py:392`, `:414`; `backend/pixel/portal/index.html:479`.

WebSocket cleanup cancels the injector but not `sess.turn_task`, so disconnected clients can leave inference/tool work running. Interrupt handling sends cancellation through the task's CancelledError path; once synthesis has completed, an interrupt does not send `speech_cancel` even if the browser still has queued audio. The browser drops the first 100 source references whenever its list exceeds 200, without ensuring those sources have finished, preventing complete cancellation of a large queued reply.

Cancel and await session tasks on disconnect, explicitly send playback cancellation on every interrupt, and retain source handles until `onended`.

### F16 — P2: Unvalidated settings can persistently break a household
Evidence: `backend/pixel/settings.py:36`, `:65`.

Settings coerce some scalars but do not validate ranges, timezones, color formats or nested object types. An invalid timezone persists and later breaks time-dependent status/prompt paths; a non-dict tone can break prompt construction. Numeric limits can be negative or arbitrarily large. Validate a complete typed patch before any writes and return a 4xx error without persisting invalid state.

### F17 — P2: Portal HTML interpolation trusts device telemetry
Evidence: `backend/pixel/portal/index.html:319`, `:357`; `backend/pixel/server.py` status-message handler.

RSSI is accepted from the WebSocket without numeric validation and interpolated into `innerHTML` in device cards/details. A malicious connected device can supply markup/event-handler text to execute in the owner's portal. F01 makes that input reachable by impersonation too. Validate telemetry types and use textContent/escaping for all dynamic values. Static source finding; browser execution was not attempted.

### F18 — P2: Public health exposes identity and does not establish inference readiness
Evidence: `backend/pixel/server.py:62`; `backend/pixel/inference.py:31`.

Public `/health` returns household name and device IDs. It checks Postgres/Redis but does not check current worker readiness; startup's worker request swallows connection failures and does not reject non-success HTTP status. CI's health smoke test can pass with an unusable voice worker. Split minimal public liveness from internal readiness and authenticated diagnostics.

### F19 — P2: Deployment does not pin the tested revision
Evidence: `.github/workflows/deploy.yml`; `backend/deploy/up.sh:5`.

CI tests a checked-out event revision, then asks the server to pull current branch HEAD. A newer push between those steps can deploy code whose tests have not passed. The HTTPS smoke test also uses `curl -k`, so it cannot establish certificate validity. Deploy the exact tested SHA/image digest and validate HTTPS normally.

### F20 — P2: Backups and retention do not cover the stated platform guarantees
Evidence: `backend/deploy/backup.sh`; `docs/PLAN.md` P3 and D13; `backend/pixel/repo.py:224`.

The backup script captures Postgres only. Firmware files live in a separate volume, so restoring release metadata does not restore downloadable binaries. Off-host B2 mirroring silently becomes optional when rclone is absent/unconfigured. Device log/event/session cleanup jobs are absent, despite 14/90-day retention promises. The roadmap correctly leaves offsite backups/restore testing and retention unfinished.

Back up firmware and required operational configuration securely, monitor backup/restore success, and implement scheduled retention. Actual server backup configuration and restore integrity were not checked.

## Audit of existing Voice Lab findings

Inspected `docs/VOICE_LAB.md`, the lab measurement code, and local JSONL artifacts: **98 STT rows, 67 TTS rows, 61 turn rows**. These are historical local measurements, not a fresh engine benchmark. They mix configurations and cold/warm states; pooled medians below are descriptive, not controlled comparisons.

| Existing claim | Evidence / audit conclusion |
|---|---|
| Keep Piper for now | Supported as a pragmatic local latency choice: 20 saved TTS rows, median TTFA about 117 ms and RTF 0.045. Voice quality remains subjective. |
| Parakeet around 250 ms and faster than base Whisper | Synthetic-audio records support this: Parakeet median 254 ms vs base Whisper 333 ms, 10 records each. It does not establish the same benefit on the deployed VM. |
| No real microphone measurement | Too broad: there are 58 rows tagged `mic` (31 Parakeet, 15 base Whisper, 12 distil). None have reference text or WER, so **real-mic accuracy is still unvalidated**, though latency was measured. Mic medians: 450 / 485 / 1148 ms respectively. |
| Zero WER establishes a clear STT winner | Zero WER applies to small clean synthetic/reference sets; it cannot establish robustness to the owner's accent, room noise, clipping or echo. |
| Whole-turn first audio about 450 ms | Not a defensible general end-of-speech figure. Saved Parakeet/Piper/energy median is 637 ms over six rows; typed runs skip STT but still carry the STT name in their stack label. Timers begin inside run_turn, after VAD has yielded the utterance, and do not measure last spoken word to audible playback. |
| Chunker caps 160 characters | False for long inputs; reproduced in F13. |
| Echo-proof barge-in | RMS/duration thresholds are heuristics, not proof of echo rejection. Controlled loudspeaker/room tests and false-interruption rates are missing. |
| GPU candidates and cost estimates | Saved cold/warm experiments are useful observations; several candidates were explicitly unmeasured/undeployed. Do not treat listed costs, credits, licenses, rankings or vendor capability claims as current verified facts; this local audit did not revalidate them externally. |
| Mac with two threads predicts a two-vCPU VM | Useful screening only. Thread limits do not reproduce cloud CPU, contention, memory or network conditions. Production conclusions need measurements on the target host. |

Keep the Piper decision provisional. The next meaningful experiment is referenced real-microphone speech plus noise/echo tests on the target worker, reporting p50/p95 latency from the last spoken word to the first audible phoneme. Preserve engine version, hardware, thread count, cold/warm state, input mode and cancellation state with each run.

## Plan and implementation reconciliation

- Pixel-Lite currently advertises `speaker=false` and `mic=false`; binary audio is counted, not played by firmware. The repository is not yet an always-listening physical voice companion. This is consistent with unfinished P4, but the README can imply otherwise.
- Clause chunking, queued TTS, pauses and portal chunk states are now committed in `3830251`; they are present but have F13/F14 defects. Parakeet is not ported into the production worker.
- SoftAP and OTA exist, but “verified provisioning” does not cover the current default TLS regression. OTA hashing does not provide the stated trusted update protection while TLS verification is disabled.
- README still describes Modal deployment; PLAN contains stale Oracle/domain/cutover text alongside newer Hetzner notes. Use a single current setup/runbook and label historical notes.
- Pixel-3S, camera/recognition, mic/speaker bring-up, wake word and plant node work remain roadmap items, not implemented end-to-end capabilities.
- This audit does not certify live Google login, DNS, server hardening, stopped Modal apps, physical board connectivity, or uptime from historical checkboxes.

## Validation and remaining coverage

- Fresh temporary Postgres database: Alembic migration to head succeeded; **10/10 backend tests passed**. Database removed after audit.
- Firmware: PlatformIO build succeeded; RAM 52,700 / 327,680 bytes (16.1%); flash 1,130,057 / 1,966,080 bytes (57.5%). No upload.
- Six focused checks reproduced anonymous say, foreign fact mutation, paired-device code regeneration, prior-owner prompt-history retention, anonymous simulator readiness, and chunk overflow. Network queues were mocked; database writes used the temporary database only.
- Existing tests are predominantly repository and helper tests. Missing coverage includes HTTP/WS access control, two-household adversarial cases, pairing/rehome lifecycle, firmware authorization, OTA failure paths, browser playback cancellation and end-to-end audio timing.
- No production traffic, private transcript contents, or credential values were needed in this report. No claim that every possible defect has been found.

Priority: fix F01–F10 before trusting public multi-user use; then bound resource use and fix provisioning/OTA/voice behavior. Add regression tests at the API and protocol boundaries, not only helper-level tests.
