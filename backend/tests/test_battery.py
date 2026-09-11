from pixel import battery


def test_silent_on_first_report_same_state_or_unknown():
    said = {}
    assert battery.decide(None, "charging", said, 1000, 12) is None
    assert battery.decide("unknown", "charging", said, 1000, 12) is None
    assert battery.decide("battery", "battery", said, 1000, 12) is None
    assert battery.decide("charging", "unknown", said, 1000, 12) is None
    assert said == {}


def test_transitions_and_cooldowns():
    said = {}; T = 1_000_000
    assert battery.decide("battery", "charging", said, T, 12) == "plugged"
    assert battery.decide("charging", "battery", said, T + 10, 12) is None          # unplugging is not worth a remark
    assert battery.decide("battery", "charging", said, T + 20, 12) is None          # within the plugged cooldown
    assert battery.decide("charging", "full", said, T + 5000, 12) == "full"
    assert battery.decide("full", "battery", said, T + 6000, 12) is None
    assert battery.decide("battery", "low", said, T + 9000, 12) == "low"
    assert battery.decide("low", "critical", said, T + 9100, 12) == "critical"
    assert battery.decide("battery", "critical", said, T + 9200, 12) is None          # critical cooldown


def test_quiet_hours_except_critical():
    said = {}
    assert battery.decide("battery", "charging", said, 1_000_000, 23) is None
    assert battery.decide("charging", "full", said, 1_000_000, 6) is None
    assert battery.decide("low", "critical", said, 1_000_000, 23) == "critical"
