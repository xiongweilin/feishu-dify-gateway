from __future__ import annotations

from cloud.watchdog import WatchdogState, transition


def test_watchdog_requires_duration_and_failures() -> None:
    state = WatchdogState()
    assert transition(state, False, 1000) == "none"
    assert transition(state, False, 1100) == "none"
    assert transition(state, False, 1299) == "none"
    assert transition(state, False, 1300) == "failure"


def test_watchdog_sends_recovery_once() -> None:
    state = WatchdogState(failures=4, first_failure_at=1000, notified=True, last_notice_at=1300)
    assert transition(state, True, 1400) == "recovery"
    assert transition(state, True, 1500) == "none"
