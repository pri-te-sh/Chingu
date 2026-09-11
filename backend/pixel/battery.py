"""Battery awareness: turns a device's inferred battery state into rare, in-character remarks.

The device reports battery_state in its status messages ("battery" | "charging" | "full" | "low" | "critical" | "unknown").
`decide()` is pure so the policy is testable: it only speaks on real transitions, never on the first report after a
(re)boot, never twice within a cooldown, and not at night except when the battery is about to die."""

COOLDOWN_S = {"plugged": 1800, "full": 7200, "low": 3600, "critical": 1800}
QUIET_FROM, QUIET_TO = 22, 8          # local hours; critical warnings ignore this

PROMPTS = {
    "plugged": "(event: you were just plugged in and your battery started charging, at {pct}%. React out loud in one short, "
               "in-character sentence, like someone hungry who just got fed. Don't say numbers.)",
    "full": "(event: your battery just finished charging and is full. One short, in-character sentence about feeling full and "
            "energised. Don't say numbers.)",
    "low": "(event: your battery is getting low, {pct}%. In one short, in-character sentence tell the owner you're hungry and "
           "would like to be plugged in.)",
    "critical": "(event: your battery is almost empty, {pct}%, and you'll switch off soon. One short, in-character, slightly urgent "
                "sentence asking to be plugged in right now.)",
}


def decide(prev: str | None, cur: str | None, said: dict, now: float, local_hour: int) -> str | None:
    """Which remark a state transition deserves, or None. `said` (kind -> last spoken time) is updated when one is chosen."""
    if not prev or not cur or cur == prev or prev == "unknown" or cur == "unknown":
        return None
    if cur == "charging" and prev in ("battery", "low", "critical"): kind = "plugged"
    elif cur == "full" and prev == "charging": kind = "full"
    elif cur == "low" and prev == "battery": kind = "low"
    elif cur == "critical" and prev in ("battery", "low"): kind = "critical"
    else: return None
    if kind != "critical" and (local_hour >= QUIET_FROM or local_hour < QUIET_TO):
        return None
    if now - said.get(kind, 0) < COOLDOWN_S[kind]:
        return None
    said[kind] = now
    return kind
