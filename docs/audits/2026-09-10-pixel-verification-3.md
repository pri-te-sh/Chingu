# Pixel security verification — third pass

Commit: `08a335b` (includes `8cbdf07` and firmware `5339626`). Voice Lab excluded. This verifies the local checkout, not the deployed revision or production database contents.

**Result:** the eight previously reported failures have fixes in source; lifecycle regression tests pass. Two additional authorization cases remain reproducible, so this is not an unconditional security sign-off.

## Remaining findings

### V1 — P1: Existing WebSocket retains prior-household access after ownership changes

Locations: `backend/pixel/server.py:321`, `:344`, `:364–365`; Session caches household at construction.

An authenticated connection binds `sess.hid` for its lifetime. Opening a second connection with the same valid device identity key but no household token releases the device for re-pairing. That release does not invalidate the first connection. After the device is claimed into household B, the original socket can still invoke respond with household A's configuration. The old connection therefore retains a path to A's memory and scoped conversation history even though the persistent device row now belongs to B.

Reproduced entirely in an isolated database and Redis:

1. Connect a keyed device with its valid household-A token.
2. Open a second connection with the valid device key but no token; receive a pairing code.
3. Use the actual HTTP claim route with household B's session cookie; assert the database owner becomes B.
4. Submit a text message over the original socket; confirm respond receives household A's scope.

The response generator was replaced with a scope-reporting stub; no inference calls or private contents were needed. This requires a previously valid device session and identity key, not an anonymous attacker with no credentials.

Fix: invalidate **all** device sessions and in-flight tasks when authorization changes, and check current ownership/token generation before each new turn. A single dictionary entry or competing consumers of one Redis inbox is not a reliable broadcast revocation mechanism. Include a concurrent-socket transfer regression test.

### V2 — P1, conditional: Token-less legacy rows still permit anonymous authentication

Location: `backend/pixel/server.py:315–318`.

The grandfather branch issues a device token when household_id and paired_at exist but token_hash and device_key_hash are absent. No credential is required: a hello naming the device ID receives `paired` with a new token and then `ready`. Requiring a historical timestamp does not authenticate the caller.

Reproduced with an isolated row matching that legacy state, sending neither cookie, token nor identity key. The previous key-planting attack against a token-bearing legacy record **is fixed**; this is the separate token-less compatibility case.

Production applicability is unknown: no production rows were inspected. If no such rows exist, this branch is dormant. Remove it or use an explicit authenticated migration/re-pair process; inspect counts of affected rows through an authorized operational check, without exposing credentials.

## Previous re-audit findings

| Finding | Verification |
|---|---|
| R1 — attacker enrolls legacy key before token check | Fixed for token-bearing legacy rows; test confirms denial without mutation and successful enrollment with valid token. V2 is a separate compatibility branch. |
| R2 — archived device reconnect restores old household | Fixed for reconnect: archive clears ownership and reconnect requires pairing; regression passes. Existing-session invalidation still needs V1. |
| R3 — pending retention deletes history | Fixed: previous-owner/archive/history exclusions protect historical devices; regression passes. |
| R4 — persona survives supported transfer | Fixed for new unpair/claim operations: previous owner retained and cross-owner persona reset; tests cover same-owner preservation too. Migration does not reconstruct provenance already lost before this change. |
| R5 — false healthy responses | Fixed: Redis PING and HTTP 503 on dependency failure; worker-failure regression passes. |
| R6 — incomplete backup mirror/restore | Source corrected: dump, firmware and env mirrored; partial artifacts tracked as failures; restore accepts additional artifacts. No real offsite backup/restore drill performed. |
| R7 — anonymous registrations exhaust capacity | Per-IP rate limit and pairing-session TTL added alongside cap. Source verified; distributed abuse/global-cap concurrency were not load-tested. |
| R8 — explicit scheme retains old port | Fixed in source: explicit schemes select 80/443 unless an explicit port is given. Firmware builds. |

Additional fixes verified in source: pause duration included in audio_s; policy-close-code assertions replace catch-all test acceptance; OTA has idle and total download limits and a dedicated task for TLS download.

## Validation and boundaries

- New temporary PostgreSQL database migrated through 0005.
- Full backend suite: **26 passed, 25 warnings**, including deprecations and SQLAlchemy connection-cleanup warnings.
- Both new findings reproduced with local TestClient, isolated database and separate Redis container. Test resources removed after verification.
- PlatformIO build passed: RAM 54,852 / 327,680 bytes (16.7%); flash 1,139,433 / 1,966,080 bytes (58.0%). No firmware upload.
- Repository notes report successful hardware OTA to 0.4.3. This pass checked source/build; it did not independently reproduce the hardware test.
- Application code unchanged. No deployments, credential changes, production exploits, or Voice Lab evaluation performed.

Recommendation: retain the existing fixes and tests. Add session-wide revocation and remove/secure credential-free legacy enrollment before declaring all authorization issues closed.
