# Pixel remediation re-audit — 2026-09-10

Reviewed commit `b83a323`, including remediation `8142522`. **Voice Lab and its experimental study are excluded.** Production backend audio code remains in scope because it ships with Pixel.

Assessment: substantial improvements, but the remediation is not complete. Eight actionable findings remain below; five were reproduced against a fresh temporary database and isolated Redis. No application code was changed, production service exercised, firmware flashed, or secrets rotated.

## Remaining findings

### R1 — P0: Legacy identity-key enrollment still permits device takeover (F02)

**Location:** `backend/pixel/server.py:296–307`.

An existing device without `device_key_hash` accepts any newly supplied key before checking its existing token. In the same request, an invalid token now reaches the “identity proven, token lost” branch because the attacker's key has just been stored. The server unpairs the legitimate owner's device and returns a pairing code. The attacker can claim it, while the legitimate unit's future key is rejected.

**Reproduced:** created a paired legacy row with a valid token, connected without that token using an attacker-chosen key, received `pairing`, and verified that the owner was cleared and the attacker's key hash persisted. No real device was contacted.

Authenticate the old token before enrolling a key; make enrollment conditional/atomic and require an explicit migration procedure for rows with no valid credential. Existing tests pre-install a legitimate key and therefore miss this transition.

### R2 — P1: Removing a device does not revoke its former household access

**Locations:** `backend/pixel/repo.py:145–147`; `backend/pixel/server.py:269–310`.

Archive retains household ID but clears the token. The new admission code fetches the row directly, skipping the old archived-row handling in `get_or_create_pixel`. A removed device presenting its retained identity key enters the token-less “grandfathered” branch, receives a new token, and is admitted to its old household. It remains archived and hidden from normal Pixel lists.

**Reproduced:** archived a keyed physical device, reconnected with its valid key and no token, received `paired` followed by `ready`, and confirmed the row still belonged to the former household with `archived=true` and a fresh token hash. On physical firmware, the `paired` response triggers a reconnect; the newly issued token then restores access.

Handle archived state explicitly before token issuance. Removal must end household authorization; re-registration must require a new claim and must not revive old access implicitly.

### R3 — P1: Pending-device retention can delete retained conversation history (F20 regression)

**Locations:** `backend/pixel/repo.py:111–115`, `:141–142`; cascading pixel foreign keys in `backend/pixel/models.py`.

`expire_pending()` interprets `household_id IS NULL AND paired_at IS NULL` as “never claimed.” However, `unpair()` clears both fields on previously used devices too. Once last_seen_at is older than seven days, retention deletes the pixel and cascades its historical turns, including turns still owned by the previous household. Current admission calls `touch_pixel`, which does not update last_seen_at, so that timestamp is not reliable evidence of inactivity either.

**Reproduced:** created a household-owned pixel and turn, unpaired it, set last_seen_at eight days back, ran expire_pending, and confirmed both pixel and conversation history disappeared.

Use a durable never-claimed marker or exclude devices with historical data. Preserve turns independently of disposable registration rows and refresh last_seen_at after authenticated activity.

### R4 — P1: Normal ownership transfer retains the previous owner's persona (F09 partial)

**Location:** `backend/pixel/repo.py:131–142`.

The persona reset only executes if the previous household is neither null nor the destination. Actual claims are now allowed only after unpairing, which sets the previous household to null. Therefore the supported unpair → claim flow skips the reset. Private facts embedded in custom persona text, plus prior owner's settings, carry over.

**Reproduced:** configured a private persona under household A, unpaired, paired into B, and read the unchanged persona. The separate transcript fix is correct: prompt history now filters by both household and pixel.

Track previous ownership across unpairing or clear personal configuration at the ownership boundary. Test the real route sequence rather than direct paired-to-paired reassignment.

### R5 — P2: Health checks still miss failures (F18 partial)

**Locations:** `backend/pixel/server.py:72–80`; `backend/pixel/bus.py:25–34`; `.github/workflows/deploy.yml` smoke test; Compose healthcheck.

`presence_all([])` creates a client but performs no Redis request because the key list is empty, so Redis is always reported healthy. A failed worker or database still produces HTTP 200 with `ok:false`, while both Docker's curl check and CI's smoke test accept HTTP success alone.

**Reproduced:** a mocked unavailable Redis performed zero commands; health reported Redis true. A mocked unavailable worker produced `ok:false` with HTTP 200.

Use Redis PING and return HTTP 503 for failed readiness, or have every consumer explicitly validate the JSON result. Separate liveness if it must remain HTTP 200.

### R6 — P2: Offsite backups and restore remain incomplete (F20 partial)

**Locations:** `backend/deploy/backup.sh:9–14`; `backend/deploy/restore.sh`.

Firmware and .env backups are now created locally, but the rclone command still copies only `$F`, the Postgres dump. Losing the host therefore loses the additional artifacts even when the log says “mirrored to b2.” The restore script restores only the database. Firmware archive errors are also swallowed and downgraded to an informational echo.

**Source-confirmed; no backup or destructive restore was run.** Mirror a complete backup set with appropriate protection for secrets, fail/report partial backup sets, and document/test restoration of all artifacts.

### R7 — P2: The global pending-device limit can lock out legitimate onboarding (F11 partial)

**Locations:** `backend/pixel/server.py:38`, `:288–292`; `backend/pixel/repo.py:107–115`.

Anonymous registrations consume one of 50 global slots. There is still no per-source rate limit or admission proof. One caller can occupy the entire allowance with distinct IDs, then legitimate new hardware is rejected until cleanup (nominally seven days) or manual intervention. The cap bounds database growth but turns abuse into a global onboarding outage. Concurrent count/check/insert operations also do not enforce the cap atomically.

**Source-confirmed; no flood test performed.** Combine bounded registration with per-source quotas/rate limits, short-lived pending sessions, atomic capacity enforcement and a recovery path for legitimate owners.

### R8 — P2: Explicit provisioning schemes retain the wrong hidden port (F08 partial)

**Location:** `src/net.cpp:73–80`.

The bare-host default TLS bug is fixed. However, entering `http://local-host` on the default production setup form changes TLS to false while preserving hidden port 443. Conversely, changing an existing HTTP configuration to `https://host` can preserve the old HTTP port. The advertised explicit-scheme input therefore produces an incompatible host/port/scheme unless users also type a port.

**Source-confirmed; not hardware-tested.** When an explicit scheme has no explicit port, select its default port. Preserve hidden defaults only for bare-host inputs. Validate the parsed endpoint before saving.

## Original finding disposition

“Addressed” means the original code defect is corrected in this checkout; it does not certify deployment or hardware behavior.

| Original | Re-check status |
|---|---|
| F01 — simulator/type authentication bypass | Addressed: existing type is authoritative, simulator cookie and household checks present; access tests pass. Legacy physical admission has separate R1. |
| F02 — pairing takeover | Still open: R1. Fully keyed devices reject wrong identity keys, but legacy enrollment is unsafe. |
| F03 — anonymous say | Addressed: authenticated household-scoped route; tests pass. |
| F04 — foreign record mutations | Addressed: household predicates on repository writes; cross-household tests pass. Some delete routes deliberately return success for a no-op rather than 404, which is not an isolation failure. |
| F05 — global firmware/drain permissions | Addressed: require_admin gates and tests present. |
| F06 — tracked credentials/build embedding | Removed from tracked tree; secrets header no longer compiled. No history entries for the backup were found via git log --all in this checkout. Cannot certify external clones/caches or credential rotation; remediation notes still call for Wi-Fi password rotation. |
| F07 — insecure TLS | Addressed in source: CA-verified WebSocket/OTA and SNTP present; firmware builds. Certificate rejection/rollover and boot behavior need hardware validation. |
| F08 — provisioning | Default bare host fixed; explicit-scheme port handling still open in R8. |
| F09 — transfer privacy | Transcript query fixed; persona reset incomplete, R4. |
| F10 — extraction-generated foreign IDs | Addressed: prompt-owned ID membership checks and household SQL predicates present. |
| F11 — registration quotas | Partial: global cap/expiry added; R7 remains. |
| F12 — OTA retry/stall loop | Original tight retry and zero-data stall fixed by attempt timestamp and 20-second idle timeout. There is still no total download deadline for a trickle stream; hardware failure tests remain unperformed. |
| F13 — production chunk cap | Addressed: hard fallback present; backend test passes. Experimental code excluded. |
| F14 — initial silence | First-chunk pause fixed. Inserted inter-chunk silence remains excluded from audio_s, so duration telemetry still undercounts playback. |
| F15 — cancellation | Disconnect awaits turn cancellation; late interrupts send cancel; browser keeps sources until onended. Source fixes present; real-browser audio cancellation not exercised. |
| F16 — settings validation | Original invalid timezone/color/tone paths addressed; tests verify no partial writes for invalid patches. |
| F17 — telemetry interpolation | Numeric validation and numeric rendering address the original fresh RSSI injection path. No browser attack or migration of historical malicious records tested. |
| F18 — health | Identity leak removed; readiness integration remains defective, R5. |
| F19 — deploy tested revision | Addressed: workflow passes tested SHA; deployment checks it out; curl no longer disables certificate validation. |
| F20 — backup/retention | Partial and regressed: R3 data loss and R6 incomplete offsite recovery. |

## Validation

- Fresh temporary PostgreSQL database migrated successfully through 0004.
- Full backend suite: **21 passed, 22 warnings**. Warnings included deprecations and SQLAlchemy connection-cleanup warnings; they were not silently treated as absent.
- Five focused checks reproduced R1–R5 using real repository operations and local TestClient; readiness failures were injected through mocks. Database and Redis were isolated from the running application.
- PlatformIO build passed: RAM 52,804 / 327,680 bytes (16.1%); flash 1,138,909 / 1,966,080 bytes (57.9%). No upload.
- Source review covered R6–R8 and each original production finding. No Voice Lab benchmarks or conclusions were reviewed.
- Access-test helper ws_refused catches any exception, including server bugs, rather than asserting a specific denial close code. Tighten this helper and add legacy enrollment, archive/reconnect, unpair/reclaim and retention-history cases.

Prioritize R1, R2 and R3, then R4. The current “all findings fixed” narrative should be revised until these lifecycle cases pass. This review is an audit, not a remediation or a live production certification.
