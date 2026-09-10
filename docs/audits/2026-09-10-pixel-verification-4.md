# Pixel verification — final two reported authorization cases

Reviewed commit `5aa1b9a`. Voice Lab excluded.

**Both V1 and V2 are addressed in the current checkout. No new actionable finding identified in this focused review.**

- **V1 — old WebSocket access after transfer:** release, claim, unpair and remove now revoke the registered session and cancel its running turn. New authenticated connections supersede existing ones. Authorization snapshots are checked before text/audio processing and conversation generation, with periodic checks for changes made outside the current process. The two-connection release/claim regression test passes and requires a policy close rather than accepting arbitrary exceptions.
- **V2 — anonymous token-less legacy admission:** the grandfather token-issuance branch is removed. Regression tests reject both an anonymous hello and an attacker-supplied new key against a paired token-less/key-less record, preserving its ownership.

Validation: fresh temporary PostgreSQL database migrated through 0005, separate temporary Redis container, full backend suite **28 passed, 28 warnings in 7.68 seconds**. Warnings include deprecated test/framework APIs and SQLAlchemy connection-cleanup warnings; these remain test-harness cleanup work, not a failure of the two authorization assertions. Temporary services/database removed afterwards.

Scope: source review of this change and backend regression suite. No production exploitation, live database access, deployment, application changes or hardware flashing. Firmware was unchanged by this commit, so its build was not repeated. This closes the two reported reproductions locally; it does not independently establish the deployed revision or certify absence of every possible vulnerability.
